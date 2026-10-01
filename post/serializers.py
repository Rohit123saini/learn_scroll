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
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import serializers

from .models import (
    Post, PostAnswer, PostComment, PostLike, PostMedia, PostPoll, PostPollOption,
    PostSave, Story, StoryView, StoryReaction, StorySticker,
)
from .story_music import MusicError, clean_music, parse_music_payload
from .story_stickers import StickerError, clean_stickers, create_stickers, parse_stickers_payload
from .story_sticker_responses import build_interaction_stats, sticker_payload

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
    # C4-BE — responsive image sizes + placeholder. ADDITIVE: every field that
    # existed before (`file`, `thumbnail`, width/height, ...) is unchanged, so
    # an old client that ignores the three new keys behaves exactly as before.
    #   thumb_url  ~320px  (grids, previews)      medium_url  ~720px (feed cards)
    #   blurhash   placeholder string to draw while the real image loads
    # These NEVER come back as null for an image just because the variants
    # haven't been generated yet (task still queued, or an old post nobody has
    # backfilled): they fall back to the original file, so a client can always
    # render `thumb_url` / `medium_url` without a null-check ladder. Only
    # `blurhash` can be null (nothing sensible to fall back to) — draw a plain
    # colour box then.
    thumb_url = serializers.SerializerMethodField()
    medium_url = serializers.SerializerMethodField()
    blurhash = serializers.SerializerMethodField()

    class Meta:
        model = PostMedia
        fields = [
            "id", "media_type", "file", "thumbnail", "file_name",
            "file_size_bytes", "mime_type", "width", "height",
            "duration_seconds", "display_order", "caption",
            "thumb_url", "medium_url", "blurhash",
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

    # --- C4-BE ---------------------------------------------------------
    def _url(self, field_file):
        return _absolute_file_url(self.context.get("request"), field_file)

    def get_thumb_url(self, obj):
        if obj.media_type in ("image", "gif"):
            # 320px variant -> original. (An animated GIF has a still thumb_320
            # too, so grids don't autoplay dozens of GIFs.)
            return self._url(obj.thumb_320) or self._url(obj.file)
        # Video: its poster frame (same file as `thumbnail`, so a client can
        # use thumb_url for every media type). Documents/audio: None.
        return self._url(obj.thumbnail)

    def get_medium_url(self, obj):
        if obj.media_type in ("image", "gif"):
            # medium_720 is intentionally empty for animated images, so those
            # (and not-yet-processed rows) get the original file.
            return self._url(obj.medium_720) or self._url(obj.file)
        return None

    def get_blurhash(self, obj):
        return obj.blur_hash or None


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


class PollVoteRequestSerializer(serializers.Serializer):
    """TASK G6 — body of POST /post/<post_id>/poll/vote/."""

    option_id = serializers.UUIDField()


class PostAnswerSerializer(serializers.ModelSerializer):
    """TASK G6 — one answer on a 'doubt' (Ask-a-doubt) post."""

    user = serializers.SerializerMethodField()
    is_own_answer = serializers.SerializerMethodField()

    class Meta:
        model = PostAnswer
        fields = [
            "id", "user", "content", "is_best_answer", "likes_count",
            "created_at", "is_own_answer",
        ]
        read_only_fields = fields

    def get_user(self, obj):
        request = self.context.get("request")
        return {
            "id": str(obj.user.id),
            "username": obj.user.username,
            "profile_picture": get_profile_pic_url(obj.user, request),
        }

    def get_is_own_answer(self, obj):
        request = self.context.get("request")
        return bool(
            request and request.user.is_authenticated and request.user.id == obj.user_id
        )


class PostAnswerCreateSerializer(serializers.Serializer):
    """TASK G6 — body of POST /post/<post_id>/answers/."""

    content = serializers.CharField(max_length=5000)

    def validate_content(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Answer can't be empty.")
        return value


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


# ---------------------------------------------------------------------------
# Category validation — single source of truth is Post.CATEGORY_CHOICES.
#
# Used by BOTH create paths: PostCreateSerializer (normal multipart create)
# and post_chunked_upload_init (views.py), which used to store whatever the
# client sent with no validation at all. Accepts the key case-insensitively
# ("Tech", " tech ") and also the human label ("Technology"), returns the
# canonical lowercase key, or None if it isn't a valid category.
# ---------------------------------------------------------------------------
def normalize_category(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    for key, label in Post.CATEGORY_CHOICES:
        if text == key or text == label.lower():
            return key
    return None


def category_error_message(value):
    allowed = ", ".join(key for key, _ in Post.CATEGORY_CHOICES)
    if value is None or str(value).strip() == "":
        return f"Category is required. Allowed values: {allowed}."
    return f'"{value}" is not a valid category. Allowed values: {allowed}.'


class CategoryField(serializers.ChoiceField):
    """`Post.category` as a serializer field with normalisation + a clear
    error message (DRF's default is just '"x" is not a valid choice.')."""

    default_error_messages = {"invalid_category": "{message}"}

    def __init__(self, **kwargs):
        super().__init__(choices=Post.CATEGORY_CHOICES, **kwargs)

    def to_internal_value(self, data):
        key = normalize_category(data)
        if key is None:
            self.fail("invalid_category", message=category_error_message(data))
        return key


class PostCreateSerializer(serializers.ModelSerializer):
    # Optional on purpose: Post.category defaults to 'general' when omitted.
    category = CategoryField(required=False)
    media_files = serializers.ListField(child=serializers.FileField(), write_only=True, required=False)
    # 🔥 FIX (root cause of images rendering as a generic file/document tile
    # instead of an actual visible image in the feed): this was a plain
    # `ListField`, but the Flutter client sends it the same way it sends
    # `media_captions`/`poll_options` — `jsonEncode(mediaTypes)` into ONE
    # multipart form field (see api_service.dart's createPost():
    # `request.fields['media_types'] = jsonEncode(mediaTypes)`). A plain
    # `ListField` over multipart data resolves to `getlist('media_types')`,
    # which is a ONE-element list containing that whole JSON string — not
    # the decoded per-file array. `create()` below used to bypass
    # `validated_data` entirely and read `request.data.getlist("media_types")`
    # directly for exactly this reason, but that has the identical bug: for
    # idx 0 it stored the literal string `'["image","video",...]'` (not
    # "image") as `PostMedia.media_type`, and every later attachment
    # (idx >= 1) silently defaulted to "image" regardless of its real type.
    # Switching to `_JSONEncodedListField` (already used for
    # `media_captions`/`poll_options` — same client-side pattern) decodes it
    # properly, so `create()` can now just use `validated_data`.
    media_types = _JSONEncodedListField(
        child=serializers.CharField(), write_only=True, required=False,
    )
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

    def validate_post_type(self, value):
        # `repost` is a valid Post.post_type now, but a repost only makes
        # sense with an `original_post` — that's set exclusively by
        # POST /post/<id>/repost/ (PostRepostAPIView), never by this
        # serializer, which has no original_post field at all.
        if value == "repost":
            raise serializers.ValidationError("Use POST /post/<id>/repost/ to repost.")
        return value

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
        # TASK G6 — a doubt post's "question" IS its content (no separate
        # question field needed — same field a text post already uses).
        if post_type == "doubt" and not content.strip():
            raise serializers.ValidationError(
                {"content": "Doubt post me apna sawaal likhna zaroori hai."}
            )
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
        # 🔥 FIX — `media_types` now goes through `_JSONEncodedListField`
        # (see field declaration above), so it's properly decoded in
        # `validated_data` same as `media_captions`/`poll_options`; no more
        # reading the raw, un-decoded `request.data.getlist(...)`.
        media_types = validated_data.pop("media_types", [])
        media_captions = validated_data.pop("media_captions", [])
        poll_options = validated_data.pop("poll_options", None)
        validated_data.pop("media_files", None)

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

        # 🔥 FIX: PostCreateAPIView calls `serializer.save(user=request.user)`,
        # which merges `user` into `validated_data` — passing it again as an
        # explicit kwarg below raised "TypeError: create() got multiple
        # values for keyword argument 'user'" on EVERY create, and the view's
        # blanket `except Exception` turned that into the generic 500
        # "Failed to create post" regardless of which category was sent.
        validated_data.pop("user", None)
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
    # TASK G6 — mirrors `poll` above: None/0 for every non-doubt post
    # (the common case), no extra query on the feed's hot path beyond the
    # single `.answers.filter(...)` when the post actually is a doubt.
    best_answer = serializers.SerializerMethodField()
    scheduled_at = serializers.SerializerMethodField()
    # 🔥 NEW — Repost feature. `original_post` is nested (not just an id)
    # so a repost card can render the embedded preview (author, media
    # thumbnail, caption) without a second request. Deliberately reuses
    # THIS SAME serializer class (`PostListSerializer`, not a stripped-
    # down "preview" one) recursively — see `get_original_post` below for
    # why that's safe (one level deep, never actually infinite) and
    # `PostRepostAPIView` (views.py) for why `original_post` can never
    # itself have an `original_post` in practice (chain is flattened at
    # repost time), which is what keeps this at exactly one level. As a
    # second line of defence `get_original_post` also refuses to nest
    # past one level (see `_repost_nested`), so a chain that ever slips
    # in via the admin or a script can't recurse.
    original_post = serializers.SerializerMethodField()
    is_reposted_by_me = serializers.SerializerMethodField()

    # DISCOVERY MIX — which bucket of HomeFeedView's mix this card came from
    # ("following" | "recommended" | "trending"), or None everywhere else
    # (profile lists, detail, explore...). Lets the app label suggested cards.
    feed_source = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            "id", "user", "title", "content", "category", "subcategory", "post_type",
            "visibility", "hashtags", "location", "likes_count",
            "comments_count", "shares_count", "views_count", "saves_count",
            "is_liked", "is_saved", "my_reaction", "poll",
            # TASK G6 — "Ask a doubt" post type.
            "answers_count", "best_answer",
            "like_count", "confuse_count", "wrong_count", "imp_count", "explain_count",
            "is_scheduled", "scheduled_at", "created_at", "media",
            "original_post", "repost_caption", "reposts_count", "is_reposted_by_me",
            # NEW — surfaces PostEditAPIView's edits (see PostEditSerializer
            # below). Field already existed on the model; nothing exposed it.
            "is_edited", "feed_source",
        ]

    def get_feed_source(self, obj):
        return (self.context.get("feed_sources") or {}).get(obj.id)

    def get_user(self, obj):
        request = self.context.get("request")
        viewer_id = request.user.id if request and request.user.is_authenticated else None
        is_own_post = viewer_id is not None and viewer_id == obj.user_id

        # TASK 2: feed post cards need a Follow button on the author, so
        # the card knows up front whether to show "Follow" / "Following"
        # / nothing (own post). Embedded repost previews never render
        # this button, so skip the check there.
        is_following = False
        if viewer_id and not is_own_post and not self.context.get("_repost_nested"):
            following_ids = self.context.get("following_ids")
            if following_ids is not None:
                # Views that already fetched the viewer's follow list
                # (HomeFeedView, ExploreFeedAPIView) pass it in as a set
                # via get_serializer_context, so this avoids an
                # extra query per post in the feed.
                is_following = obj.user_id in following_ids
            else:
                from user_profile.models import Follow

                is_following = Follow.objects.filter(
                    follower_id=viewer_id, following_id=obj.user_id,
                    status=Follow.Status.ACCEPTED,
                ).exists()

        return {
            "id": str(obj.user.id),
            "username": obj.user.username,
            "profile_picture": get_profile_pic_url(obj.user, request),
            "is_following": is_following,
            "is_own_post": is_own_post,
        }

    def get_is_liked(self, obj):
        request = self.context.get("request")
        if self.context.get("_repost_nested"):
            # Embedded repost preview: the client never reads viewer state
            # (like/save/reaction) off it — skip the per-row queries.
            return False
        if request and request.user.is_authenticated:
            # Prefer an annotated value from the view to avoid N+1 on list pages.
            if hasattr(obj, "is_liked_annotated"):
                return obj.is_liked_annotated
            return obj.likes.filter(user=request.user).exists()
        return False

    def get_is_saved(self, obj):
        request = self.context.get("request")
        if self.context.get("_repost_nested"):
            return False
        if request and request.user.is_authenticated:
            if hasattr(obj, "is_saved_annotated"):
                return obj.is_saved_annotated
            return PostSave.objects.filter(post=obj, user=request.user).exists()
        return False

    def get_my_reaction(self, obj):
        request = self.context.get("request")
        if self.context.get("_repost_nested"):
            return None
        if request and request.user.is_authenticated:
            like = obj.likes.filter(user=request.user).first()
            if like:
                return like.reaction_type
        return None

    def get_poll(self, obj):
        request = self.context.get("request")
        if obj.post_type == "repost" or self.context.get("_repost_nested"):
            # A repost row never owns a poll, and the embedded preview
            # doesn't render one — skip the OneToOne lookup.
            return None
        try:
            poll = obj.poll
        except PostPoll.DoesNotExist:
            return None
        return PostPollSerializer(poll, context={"request": request}).data

    def get_best_answer(self, obj):
        request = self.context.get("request")
        if obj.post_type != "doubt" or self.context.get("_repost_nested"):
            return None
        best = obj.answers.filter(is_best_answer=True).first()
        return PostAnswerSerializer(best, context={"request": request}).data if best else None

    def get_scheduled_at(self, obj):
        # Named to match the write-side field (`scheduled_at` -> Task 5's
        # PostCreateSerializer), rather than exposing the raw
        # `published_at` column name on read — keeps the API's naming
        # consistent for whoever's building the "Scheduled" tab UI. None
        # once a post has actually gone live (`is_scheduled` flips False
        # at that point — see the publish-sweep note on the Post model).
        return obj.published_at if obj.is_scheduled else None

    def get_original_post(self, obj):
        """Embedded preview of the reposted post (None for a normal post).

        Reuses this same serializer, one level deep only: the nested call
        sets `_repost_nested` in its context, and a nested post never
        resolves its own `original_post` again.

        If the original can't be shown to THIS viewer (soft-deleted,
        moderated away, no longer public, or its author's account is
        private and the viewer doesn't follow them) a stub
        `{"id": ..., "is_unavailable": True}` is returned instead of the
        content, so a repost never leaks something the original's own
        visibility rules would hide. A hard-deleted original
        (`SET_NULL`) simply comes back as None on a `post_type='repost'`
        row — the client shows the same "unavailable" placeholder.
        """
        if not obj.original_post_id or self.context.get("_repost_nested"):
            return None
        request = self.context.get("request")
        viewer_id = request.user.id if request and request.user.is_authenticated else None
        original = obj.original_post

        unavailable = (
            original.is_deleted
            or original.moderation_status != "approved"
            or (original.visibility != "public" and original.user_id != viewer_id)
        )
        if not unavailable and original.user.is_private and original.user_id != viewer_id:
            from user_profile.models import Follow

            unavailable = not (
                viewer_id
                and Follow.objects.filter(
                    follower_id=viewer_id, following_id=original.user_id,
                    status=Follow.Status.ACCEPTED,
                ).exists()
            )
        if unavailable:
            return {"id": str(original.id), "is_unavailable": True}

        return PostListSerializer(
            original, context={**self.context, "_repost_nested": True},
        ).data

    def get_is_reposted_by_me(self, obj):
        request = self.context.get("request")
        if obj.post_type == "repost":
            # Reposting a repost targets its root original, so the flag that
            # matters for a repost card lives on its embedded `original_post`.
            return False
        if request and request.user.is_authenticated:
            # Same annotated-value escape hatch as is_liked / is_saved.
            if hasattr(obj, "is_reposted_annotated"):
                return obj.is_reposted_annotated
            return Post.objects.filter(
                original_post_id=obj.id, user=request.user, is_deleted=False,
            ).exists()
        return False


# ---------------------------------------------------------------------------
# REELS - lean payload for GET /post/reels/ (post/reels_views.py).
#
# Deliberately NOT PostListSerializer: a full-screen vertical player needs one
# video, the caption, the author strip, the four counters and the viewer's own
# state - no poll / doubt / repost / scheduling / category machinery.
#
# N+1: `media` comes from the view's prefetch, `author.is_following` from the
# ONE following-id set the view already loaded (Home does the same), and
# is_liked / my_reaction / is_saved from two batched queries per page passed in
# the context (`reel_reactions`: {post_id: reaction_type}, `reel_saved_ids`:
# set). When those context keys are missing (serializer used on its own) each
# falls back to a single query per post, so the class is safe everywhere.
# ---------------------------------------------------------------------------
def reel_video_media(post):
    """The video row a reel plays: the first video media with a known duration
    (that is the row the eligibility rules are checked on), else the first video
    row. Reads the prefetched `post.media` - no query. None if there is none."""
    first = None
    for media in post.media.all():
        if media.media_type != "video":
            continue
        if media.duration_seconds:
            return media
        if first is None:
            first = media
    return first


def _absolute_file_url(request, file_field):
    if not file_field:
        return None
    try:
        url = file_field.url
    except ValueError:
        return None
    return request.build_absolute_uri(url) if request is not None else url


class ReelSerializer(serializers.ModelSerializer):
    video = serializers.SerializerMethodField()
    caption = serializers.SerializerMethodField()
    author = serializers.SerializerMethodField()
    counts = serializers.SerializerMethodField()
    is_liked = serializers.SerializerMethodField()
    my_reaction = serializers.SerializerMethodField()
    is_saved = serializers.SerializerMethodField()
    feed_source = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            "id", "video", "caption", "hashtags", "author", "counts",
            "is_liked", "my_reaction", "is_saved", "feed_source",
        ]
        read_only_fields = fields

    def _viewer(self):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        return user if user is not None and user.is_authenticated else None

    def get_video(self, obj):
        media = reel_video_media(obj)
        if media is None:
            return None
        request = self.context.get("request")
        return {
            "url": _absolute_file_url(request, media.file),
            "thumbnail": _absolute_file_url(request, media.thumbnail),
            "duration": media.duration_seconds,
            "width": media.width,
            "height": media.height,
            "blur_hash": media.blur_hash or None,
        }

    def get_caption(self, obj):
        return obj.content or ""

    def get_author(self, obj):
        request = self.context.get("request")
        viewer = self._viewer()
        author = obj.user
        is_following = False
        if viewer is not None and viewer.id != obj.user_id:
            following_ids = self.context.get("following_ids")
            if following_ids is not None:
                is_following = obj.user_id in following_ids
            else:
                from user_profile.models import Follow

                is_following = Follow.objects.filter(
                    follower_id=viewer.id, following_id=obj.user_id, status=Follow.Status.ACCEPTED,
                ).exists()
        return {
            "id": str(author.id),
            "username": author.username,
            "names": (author.get_full_name() or "").strip(),
            "profile_photo": get_profile_pic_url(author, request),
            "is_following": is_following,
        }

    def get_counts(self, obj):
        return {
            "likes": obj.likes_count,
            "comments": obj.comments_count,
            "shares": obj.shares_count,
            "saves": obj.saves_count,
        }

    def _reaction(self, obj):
        viewer = self._viewer()
        if viewer is None:
            return None
        reactions = self.context.get("reel_reactions")
        if reactions is not None:
            return reactions.get(obj.id)
        return obj.likes.filter(user=viewer).values_list("reaction_type", flat=True).first()

    def get_is_liked(self, obj):
        # Same meaning as Home: any reaction counts as "liked".
        return self._reaction(obj) is not None

    def get_my_reaction(self, obj):
        return self._reaction(obj)

    def get_is_saved(self, obj):
        viewer = self._viewer()
        if viewer is None:
            return False
        saved_ids = self.context.get("reel_saved_ids")
        if saved_ids is not None:
            return obj.id in saved_ids
        return PostSave.objects.filter(post=obj, user=viewer).exists()

    def get_feed_source(self, obj):
        return (self.context.get("feed_sources") or {}).get(obj.id)


class RepostRequestSerializer(serializers.Serializer):
    """Body of POST /post/<id>/repost/ — everything is optional; an empty
    body is a plain one-tap "quick repost"."""

    MAX_CAPTION_LENGTH = 500

    repost_caption = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=MAX_CAPTION_LENGTH,
    )

    def validate_repost_caption(self, value):
        value = (value or "").strip()
        return value or None


# ===================== POST EDIT / VISIBILITY (checklist item — no =====
# PATCH/edit endpoint existed anywhere in this app before this; frontend
# had no edit UI either. Two separate serializers/views on purpose:
# editing text fields and changing who can see a post are different
# actions with different semantics (an edit marks `is_edited=True` and
# shows an "edited" label; a privacy change doesn't — same distinction
# Instagram/Twitter make), so they're separate PATCH endpoints rather
# than one that silently does both.
class PostEditSerializer(serializers.ModelSerializer):
    """Body of PATCH /post/<id>/edit/. Partial update of a post's own
    text/category fields only — never media, `visibility` (see
    PostVisibilitySerializer), `post_type`, or `original_post`. Mirrors
    PostCreateSerializer's hashtag auto-extraction/normalization so an
    edited post's tags stay consistent with how a newly-created one gets
    them."""
    category = CategoryField(required=False)
    hashtags = serializers.ListField(child=serializers.CharField(max_length=100), required=False, allow_empty=True)

    class Meta:
        model = Post
        fields = ["title", "content", "category", "subcategory", "hashtags", "mentioned_user_ids", "metadata", "location"]

    def validate_subcategory(self, value):
        if value:
            value = value.strip()
        return value or None

    def validate(self, attrs):
        # Mirrors PostCreateSerializer.validate's same rule: a text post
        # can't be edited down to empty content. Only checked when
        # `content` was actually part of this PATCH — editing e.g. just
        # the category shouldn't require re-sending content.
        post_type = self.instance.post_type if self.instance else "text"
        if "content" in attrs and post_type == "text" and not (attrs.get("content") or "").strip():
            raise serializers.ValidationError({"content": "Text post me content required hai"})
        return attrs

    def update(self, instance, validated_data):
        if "content" in validated_data and not validated_data.get("hashtags"):
            validated_data["hashtags"] = re.findall(r"#(\w+)", validated_data["content"] or "")
        if "hashtags" in validated_data:
            seen = []
            for tag in validated_data["hashtags"]:
                normalized = tag.strip().lstrip("#").lower()
                if normalized and normalized not in seen:
                    seen.append(normalized)
            validated_data["hashtags"] = seen

        for field, value in validated_data.items():
            setattr(instance, field, value)
        if validated_data:
            instance.is_edited = True
            instance.save(update_fields=list(validated_data.keys()) + ["is_edited", "updated_at"])
        return instance


class PostVisibilitySerializer(serializers.Serializer):
    """Body of PATCH /post/<id>/visibility/ — `{"visibility": "public" |
    "connections" | "private"}`. Doesn't touch `is_edited` (a privacy
    change isn't a content edit)."""
    visibility = serializers.ChoiceField(choices=Post.VISIBILITY_CHOICES)


class PostShareRequestSerializer(serializers.Serializer):
    """Body of POST /post/<id>/share/ — internal "forward to chat"
    (checklist item 61), NOT the OS share sheet `Share.share(...)`
    already wired in singlepost.dart (that one never touches the
    backend). `conversation_id` must be a chat the caller is already a
    participant in — PostShareAPIView/share_post_to_conversation
    (services.py) 403s otherwise."""
    conversation_id = serializers.UUIDField()


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
        from .services import hidden_commenter_ids

        request = self.context.get("request")
        viewer = getattr(request, "user", None)
        viewer_id = getattr(viewer, "id", None) if viewer and viewer.is_authenticated else None

        comments = obj.comments.filter(parent=None, is_deleted=False, is_hidden=False)

        # Restrict is scoped to the post owner's restrict list, not the
        # viewer's — it's the owner's space being protected. Excluded at
        # the queryset level (not per-row) so the [:10] slice below still
        # returns up to 10 *visible* comments instead of coming up short
        # because restricted ones were filtered out after slicing.
        # A restricted user must still see their own comments exactly as
        # before — restrict is defined to be invisible to them (the helper
        # already removes the viewer from the hidden set).
        restricted_ids = hidden_commenter_ids(obj.user_id, viewer_id)
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
class _StickersField(serializers.Field):
    """`stickers` on POST /post/stories/create/ - a JSON-encoded list in the
    multipart body (multipart cannot carry an array natively). Parsing and
    the shape check live in post/story_stickers.py; the per-sticker business
    rules (mention/link/poll/question) run in StoryCreateSerializer.validate() once the
    story's `audience` is known."""

    def to_internal_value(self, data):
        try:
            return parse_stickers_payload(data)
        except StickerError as exc:
            raise serializers.ValidationError(exc.as_text())

    def to_representation(self, value):  # write-only in practice
        return value


class _MusicField(serializers.Field):
    """`music` on POST /post/stories/create/ - a JSON-encoded object in the
    multipart body (or the object itself for a JSON body). Every rule lives in
    post/story_music.py. '' / 'null' / {} mean "no music"."""

    def to_internal_value(self, data):
        try:
            return clean_music(parse_music_payload(data))
        except MusicError as exc:
            raise serializers.ValidationError(exc.as_text())

    def to_representation(self, value):  # write-only in practice
        return value


class StoryCreateSerializer(serializers.ModelSerializer):
    # STORIES UPGRADE - PART 2: optional overlays (mention / link / poll / question).
    stickers = _StickersField(required=False, write_only=True)
    # STORIES UPGRADE - PART 3a: optional background track (CC0, from the music search).
    music = _MusicField(required=False, write_only=True, allow_null=True)

    class Meta:
        model = Story
        fields = [
            "id", "media", "media_type", "caption", "audience", "expires_at", "created_at",
            "stickers", "music",
        ]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {"expires_at": {"required": False}, "audience": {"required": False}}

    def validate_media(self, file):
        # Reuses the same allowlist/size limit as post media — a story is
        # just a short-lived image/video, no reason to allow anything a
        # regular PostMedia upload wouldn't.
        return validate_media_file(file)

    def validate(self, attrs):
        raw_stickers = attrs.pop("stickers", None) or []
        audience = attrs.get("audience") or Story.AUDIENCE_EVERYONE
        try:
            attrs["_clean_stickers"] = clean_stickers(
                raw_stickers, self.context["request"].user, audience,
            )
        except StickerError as exc:
            raise serializers.ValidationError({"stickers": [exc.as_text()]})
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        cleaned = validated_data.pop("_clean_stickers", [])
        # Story + stickers are one unit: if a sticker row fails to write, the
        # story is not created half-decorated.
        with transaction.atomic():
            story = Story.objects.create(user=request.user, **validated_data)
            if cleaned:
                create_stickers(story, cleaned)

        mentioned = [c.mentioned_user for c in cleaned if c.mentioned_user is not None]
        if mentioned:
            from .services import notify_story_mentions  # local: services imports serializers-adjacent code

            notify_story_mentions(story, mentioned)
        return story


def story_sticker_prefetch():
    """Prefetch for `Story.stickers` with the mentioned user joined in, so
    serialising N stories costs 2 queries for stickers, not 1 + N."""
    return Prefetch("stickers", queryset=StorySticker.objects.select_related("mentioned_user"))


class StoryStickerSerializer(serializers.ModelSerializer):
    """One overlay on a story. Placement is the same for every kind; the
    kind-specific content is in `data`:

        mention  -> {"user": {"id", "username", "profile_picture"}}
        link     -> {"url", "label", "host"}
        poll     -> {"question", "options": [..], "my_vote": int|null,
                     "results": {"counts": [..], "total": n} | null}
                    (`results` only for the owner, or once the viewer has voted)
        question -> {"prompt", "my_answered": bool, "answers_count": n|null}
                    (`answers_count` only for the owner)

    Context (all optional): `request`, `sticker_stats` (from
    build_interaction_stats - computed here when missing) and `story_owner_id`
    (defaults to the sticker's story owner)."""
    data = serializers.SerializerMethodField()

    class Meta:
        model = StorySticker
        fields = ["id", "kind", "x", "y", "rotation", "scale", "z_index", "data"]

    def get_data(self, obj):
        if obj.kind == StorySticker.KIND_MENTION:
            user = obj.mentioned_user
            if user is None:
                return {}
            return {
                "user": {
                    "id": str(user.id),
                    "username": user.username,
                    "profile_picture": get_profile_pic_url(user, self.context.get("request")),
                }
            }
        if obj.kind in (StorySticker.KIND_POLL, StorySticker.KIND_QUESTION):
            request = self.context.get("request")
            viewer = getattr(request, "user", None)
            viewer_id = viewer.id if viewer is not None and viewer.is_authenticated else None
            stats = self.context.get("sticker_stats")
            if stats is None:
                stats = build_interaction_stats([obj], viewer)
            owner_id = self.context.get("story_owner_id", obj.story.user_id)
            return sticker_payload(obj, stats, viewer_id, owner_id)
        return dict(obj.data or {})


def serialize_sticker(sticker, request):
    """One sticker exactly as the story endpoints show it to `request.user`
    (used by the vote / answer endpoints to return the fresh state)."""
    return StoryStickerSerializer(
        sticker, context={"request": request, "story_owner_id": sticker.story.user_id},
    ).data


class StoryMentionCandidateSerializer(serializers.Serializer):
    """A person the composer can @mention. Same row shape as the Close
    Friends screens minus `is_close_friend`."""
    id = serializers.IntegerField()
    username = serializers.CharField()
    name = serializers.SerializerMethodField()
    profile_picture = serializers.SerializerMethodField()

    def get_name(self, obj):
        full = f"{getattr(obj, 'first_name', '') or ''} {getattr(obj, 'last_name', '') or ''}".strip()
        return full or obj.username

    def get_profile_picture(self, obj):
        return get_profile_pic_url(obj, self.context.get("request"))


class StoryListSerializer(serializers.ListSerializer):
    """Serialises a page of stories with the poll / question numbers for ALL of
    them loaded in one go (instead of one query per story)."""

    def to_representation(self, data):
        items = list(data.all() if hasattr(data, "all") else data)
        request = self.context.get("request")
        viewer = getattr(request, "user", None)
        stickers = [s for story in items for s in story.stickers.all()]
        if any(s.kind in (StorySticker.KIND_POLL, StorySticker.KIND_QUESTION) for s in stickers):
            self.context["sticker_stats"] = build_interaction_stats(stickers, viewer)
        return super().to_representation(items)


class StorySerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    media = serializers.SerializerMethodField()
    is_viewed_by_me = serializers.SerializerMethodField()
    stickers = serializers.SerializerMethodField()
    music = serializers.SerializerMethodField()

    class Meta:
        model = Story
        list_serializer_class = StoryListSerializer
        fields = [
            "id", "user", "media", "media_type", "caption", "audience",
            "views_count", "is_viewed_by_me", "created_at", "expires_at",
            "stickers", "music",
        ]

    def get_music(self, obj):
        # Stored already-cleaned (post/story_music.py); NULL = no music.
        return obj.music or None

    def get_stickers(self, obj):
        # `.all()` uses the prefetch cache when the view set one up
        # (story_sticker_prefetch); otherwise it is one small query.
        stickers = list(obj.stickers.all())
        ctx = dict(self.context)
        ctx["story_owner_id"] = obj.user_id
        if "sticker_stats" not in ctx:
            request = self.context.get("request")
            ctx["sticker_stats"] = build_interaction_stats(stickers, getattr(request, "user", None))
        return StoryStickerSerializer(stickers, many=True, context=ctx).data

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


# ---------------------------------------------------------------------------
# Story reactions + viewers list — Instagram-style "who viewed / who
# reacted" screen the story owner sees under their own story.
# ---------------------------------------------------------------------------
class StoryReactionSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()

    class Meta:
        model = StoryReaction
        fields = ["id", "user", "emoji", "created_at"]

    def get_user(self, obj):
        request = self.context.get("request")
        return {
            "id": str(obj.user.id),
            "username": obj.user.username,
            "profile_picture": get_profile_pic_url(obj.user, request),
        }


class StoryViewerEntrySerializer(serializers.ModelSerializer):
    """One row per viewer, with that viewer's reaction (if any) folded in —
    same list Instagram shows under "Activity" on your own story, reaction
    emoji shown next to the viewer instead of a separate list."""
    user = serializers.SerializerMethodField()
    reaction = serializers.SerializerMethodField()

    class Meta:
        model = StoryView
        fields = ["id", "user", "viewed_at", "reaction"]

    def get_user(self, obj):
        if not obj.user_id:
            return None
        request = self.context.get("request")
        return {
            "id": str(obj.user.id),
            "username": obj.user.username,
            "profile_picture": get_profile_pic_url(obj.user, request),
        }

    def get_reaction(self, obj):
        # Folded in via context (a single query in the view, not one per
        # row) — see StoryViewersAPIView.get_serializer_context.
        reactions_by_user_id = self.context.get("reactions_by_user_id", {})
        r = reactions_by_user_id.get(obj.user_id)
        return {"emoji": r.emoji, "created_at": r.created_at.isoformat()} if r else None


# ---------------------------------------------------------------------------
# Close Friends (STORIES UPGRADE - PART 1)
# ---------------------------------------------------------------------------
class CloseFriendUserSerializer(serializers.Serializer):
    """One person row for the Close Friends screens. Works on a User
    instance; `is_close_friend` comes from the view (context set of ids) so
    the list is one extra query, not one per row."""
    id = serializers.IntegerField()
    username = serializers.CharField()
    name = serializers.SerializerMethodField()
    profile_picture = serializers.SerializerMethodField()
    is_close_friend = serializers.SerializerMethodField()

    def get_name(self, obj):
        full = f"{getattr(obj, 'first_name', '') or ''} {getattr(obj, 'last_name', '') or ''}".strip()
        return full or obj.username

    def get_profile_picture(self, obj):
        return get_profile_pic_url(obj, self.context.get("request"))

    def get_is_close_friend(self, obj):
        return obj.id in self.context.get("close_friend_ids", ())


class CloseFriendsReplaceSerializer(serializers.Serializer):
    """Body for PUT /post/close-friends/ - the full new list in one call."""
    user_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), allow_empty=True, max_length=5000,
    )

    def validate_user_ids(self, value):
        seen = []
        for v in value:
            if v not in seen:
                seen.append(v)
        return seen


# ===================== TASK 3 — INTERESTS =====================
class UserInterestsUpdateSerializer(serializers.Serializer):
    """Body for PUT /post/interests/ — replaces the caller's full interest
    set in one call (chip row toggles all get sent together, not diffed
    client-side). `categories` must be a subset of `Post.CATEGORY_CHOICES`
    keys; anything else is rejected up front rather than silently dropped.
    """
    categories = serializers.ListField(
        child=serializers.ChoiceField(choices=Post.CATEGORY_CHOICES),
        allow_empty=True,
    )

    def validate_categories(self, value):
        # De-dupe while preserving the caller's order (nice-to-have for any
        # UI that echoes selection order back).
        seen = []
        for c in value:
            if c not in seen:
                seen.append(c)
        return seen


# ---------------------------------------------------------------------------
# FEED FEEDBACK CONTROLS - PART 1 (hide + mute)
# ---------------------------------------------------------------------------
from user_profile.serializers import UserSearchSerializer as _FeedbackUserSerializer  # noqa: E402
from .models import MutedAccount as _MutedAccount, PostHide as _PostHide  # noqa: E402


class NotInterestedRequestSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(
        choices=_PostHide.Reason.choices, required=False, default=_PostHide.Reason.NOT_INTERESTED,
    )


class PostHideSerializer(serializers.ModelSerializer):
    post_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = _PostHide
        fields = ['id', 'post_id', 'reason', 'created_at']
        read_only_fields = fields


class MuteAccountRequestSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()


class MutedAccountSerializer(serializers.ModelSerializer):
    user = _FeedbackUserSerializer(source='muted_user', read_only=True)

    class Meta:
        model = _MutedAccount
        fields = ['id', 'user', 'created_at']
        read_only_fields = fields


# ---------------------------------------------------------------------------
# FEED FEEDBACK CONTROLS - PART 2 (show fewer + why am I seeing this)
# ---------------------------------------------------------------------------
from .models import FeedFeedback as _FeedFeedback  # noqa: E402
from .services import MAX_SHOW_FEWER_TARGETS as _MAX_SHOW_FEWER_TARGETS  # noqa: E402


class ShowFewerTargetSerializer(serializers.Serializer):
    """One thing to dampen. `key` is only read for kind=hashtag (which tag of
    the post); for category / author the server derives it from the post."""
    kind = serializers.ChoiceField(choices=_FeedFeedback.Kind.choices)
    key = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)


class ShowFewerRequestSerializer(serializers.Serializer):
    targets = ShowFewerTargetSerializer(many=True, allow_empty=False)
    reason = serializers.ChoiceField(
        choices=_PostHide.Reason.choices, required=False, default=_PostHide.Reason.NOT_INTERESTED,
    )

    def validate_targets(self, value):
        if len(value) > _MAX_SHOW_FEWER_TARGETS:
            raise serializers.ValidationError(f"At most {_MAX_SHOW_FEWER_TARGETS} targets per request.")
        return value


class FeedFeedbackSerializer(serializers.ModelSerializer):
    """`strength` = the weight decayed to NOW (what ranking really uses);
    `label` = display text (category name / "#tag" / "@username").
    Author labels come from `context["author_labels"]` ({user_id: username})
    so a list needs one user query, not one per row."""
    strength = serializers.SerializerMethodField()
    label = serializers.SerializerMethodField()

    class Meta:
        model = _FeedFeedback
        fields = ['id', 'kind', 'key', 'label', 'strength', 'updated_at']
        read_only_fields = fields

    def get_strength(self, obj):
        from . import feed_mix

        cfg = feed_mix.get_feedback_config()
        age_days = (timezone.now() - obj.updated_at).total_seconds() / 86400.0
        decayed = feed_mix.decay_weight(obj.weight, age_days, cfg["half_life_days"])
        return round(min(float(cfg["max_weight"]), decayed), 2)

    def get_label(self, obj):
        if obj.kind == _FeedFeedback.Kind.CATEGORY:
            return dict(Post.CATEGORY_CHOICES).get(obj.key, obj.key)
        if obj.kind == _FeedFeedback.Kind.HASHTAG:
            return f"#{obj.key}"
        username = (self.context.get('author_labels') or {}).get(obj.key)
        return f"@{username}" if username else None


class WhyReasonSerializer(serializers.Serializer):
    """Schema only (drf-spectacular) - the payload is built by post/feed_explain.py."""
    code = serializers.CharField()
    text = serializers.CharField()
    meta = serializers.DictField()


class WhyDampenedSerializer(serializers.Serializer):
    kind = serializers.CharField()
    key = serializers.CharField()
    strength = serializers.FloatField()


class WhyResponseSerializer(serializers.Serializer):
    post_id = serializers.UUIDField()
    feed_source = serializers.CharField(allow_null=True)
    reasons = WhyReasonSerializer(many=True)
    dampened = WhyDampenedSerializer(many=True)
