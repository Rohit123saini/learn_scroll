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

from . import doubts, minor_privacy, polls
from .models import (
    Post, PostAnswer, PostAttachment, PostComment, PostLike, PostMedia, PostPoll, PostPollOption,
    PostCollaborator, PostSave, PostTag, Story, StoryView, StoryReaction, StorySticker,
)
from .story_music import MusicError, clean_music, parse_music_payload
from .story_stickers import StickerError, clean_stickers, create_stickers, parse_stickers_payload
from .story_sticker_responses import INTERACTIVE_KINDS, build_interaction_stats, sticker_payload

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


from .tagging import notify_post_tags, tagged_users_payload  # noqa: E402,F401  (notify_post_tags re-exported for callers/tests)


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


class _JSONEncodedDictField(serializers.DictField):
    """DictField that also accepts a JSON-encoded string (multipart has no native object type) - same trick as
    `_JSONEncodedListField`. Used for `poll_settings`."""

    def get_value(self, dictionary):
        value = super().get_value(dictionary)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return value  # let DictField's own validation raise a clear error
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
            "duration_seconds", "display_order", "caption", "alt_text",
            "thumb_url", "medium_url", "blurhash",
            # V1 — "ready" | "pending" | "processing" | "failed" (videos only;
            # everything else is always "ready"). ADDITIVE: old clients ignore it.
            "processing_status",
            "hls_url",  # videos: HLS master playlist (null until built); `file` (MP4) is the fallback
            "sprite",  # videos: seek-bar scrub preview sprite (null until built) - see `video_sprite_payload`
        ]
        read_only_fields = ["id", "file_name", "file_size_bytes", "mime_type", "thumbnail", "processing_status"]

    def get_file(self, obj):
        request = self.context.get("request")
        if not obj.file:
            return None
        try:
            return request.build_absolute_uri(obj.file.url) if request else obj.file.url
        except ValueError:
            return None

    hls_url = serializers.SerializerMethodField()

    def get_hls_url(self, obj):
        return hls_playlist_url(self.context.get("request"), obj)

    sprite = serializers.SerializerMethodField()

    def get_sprite(self, obj):
        return video_sprite_payload(self.context.get("request"), obj)

    def get_thumbnail(self, obj):
        request = self.context.get("request")
        if not obj.thumbnail:
            # T6 - a video must always get a poster frame: queue generation now (rate limited, never raises)
            # so the NEXT response carries it; the client shows its blur/colour placeholder meanwhile.
            if obj.media_type == "video":
                from .services import ensure_video_thumbnail

                ensure_video_thumbnail(obj)
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
    """Plain option (no viewer rules) - the poll serializer below builds the viewer-aware version itself
    (vote counts / the right answer are hidden until the viewer may see them)."""

    image = serializers.SerializerMethodField()

    class Meta:
        model = PostPollOption
        fields = ["id", "text", "image", "votes_count", "display_order"]
        read_only_fields = fields

    def get_image(self, obj):
        return polls.option_image_url(obj, self.context.get("request"))


class PostPollSerializer(serializers.ModelSerializer):
    """Read-only, viewer-aware representation attached to PostListSerializer/PostDetailSerializer.

    Viewer rules (see post/polls.py):
      * `results_visible`  - live numbers: creator always; others per `results_visibility`
                             (always / after_vote / after_end). When False, `votes_count` / `total_votes_count`
                             are null and the client must not draw bars.
      * quiz verdict       - `is_correct` per option, `correct_option_ids` and `explanation` are null/"" until the
                             viewer has answered (or the quiz ended / the viewer is the creator).
      * `my_vote_option_id`  is kept for old clients (first picked option); `my_vote_option_ids` is the full set.
    """

    options = serializers.SerializerMethodField()
    is_expired = serializers.BooleanField(read_only=True)
    total_votes_count = serializers.SerializerMethodField()
    my_vote_option_id = serializers.SerializerMethodField()
    my_vote_option_ids = serializers.SerializerMethodField()
    has_voted = serializers.SerializerMethodField()
    results_visible = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()
    my_is_correct = serializers.SerializerMethodField()
    correct_option_ids = serializers.SerializerMethodField()
    explanation = serializers.SerializerMethodField()

    class Meta:
        model = PostPoll
        fields = [
            "id", "options", "total_votes_count", "expires_at",
            "is_expired", "created_at", "my_vote_option_id", "my_vote_option_ids", "has_voted",
            "allow_multiple", "max_choices", "is_anonymous", "results_visibility", "results_visible",
            "is_quiz", "is_mine", "my_is_correct", "correct_option_ids", "explanation",
        ]
        read_only_fields = fields

    # -- per-poll viewer state, computed once per serialization
    def _user(self):
        request = self.context.get("request")
        return request.user if request is not None else None

    def _state(self, obj):
        user = self._user()
        mine = polls.my_option_ids(obj, user)
        voted = bool(mine)
        return {
            "mine": mine,
            "voted": voted,
            "results": polls.results_visible_for(obj, user, voted),
            "reveal": polls.answers_revealed_for(obj, user, voted),
        }

    @staticmethod
    def _opts(obj):
        # `obj.options.all()` is a query unless prefetched; the three getters below would each run it.
        cached = getattr(obj, "_options_list", None)
        if cached is None:
            cached = obj._options_list = list(obj.options.all())
        return cached

    def get_options(self, obj):
        request = self.context.get("request")
        st = self._state(obj)
        return [
            {
                "id": str(o.id),
                "text": o.text,
                "image": polls.option_image_url(o, request),
                "votes_count": o.votes_count if st["results"] else None,
                "display_order": o.display_order,
                "is_correct": o.is_correct if st["reveal"] else None,
            }
            for o in self._opts(obj)
        ]

    def get_total_votes_count(self, obj):
        return obj.total_votes_count if self._state(obj)["results"] else None

    def get_my_vote_option_id(self, obj):
        mine = self._state(obj)["mine"]
        return str(mine[0]) if mine else None

    def get_my_vote_option_ids(self, obj):
        return [str(i) for i in self._state(obj)["mine"]]

    def get_has_voted(self, obj):
        return self._state(obj)["voted"]

    def get_results_visible(self, obj):
        return self._state(obj)["results"]

    def get_is_mine(self, obj):
        return polls.is_owner(obj, self._user())

    def get_my_is_correct(self, obj):
        if not obj.is_quiz:
            return None
        st = self._state(obj)
        if not st["voted"]:
            return None
        return polls.my_vote_is_correct(obj, self._user())

    def get_correct_option_ids(self, obj):
        if not self._state(obj)["reveal"]:
            return None
        return [str(o.id) for o in self._opts(obj) if o.is_correct]

    def get_explanation(self, obj):
        return obj.explanation if self._state(obj)["reveal"] else ""


class PollVoteRequestSerializer(serializers.Serializer):
    """Body of POST /post/<post_id>/poll/vote/.

    `option_id`  (old clients, one option)  OR  `option_ids` (list - the whole selection of a multiple-choice
    poll; a single-choice poll takes exactly one). The server enforces the poll's own limits."""

    option_id = serializers.UUIDField(required=False)
    option_ids = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=False, max_length=6)

    def validate(self, attrs):
        ids = list(attrs.get("option_ids") or [])
        if attrs.get("option_id") is not None and attrs["option_id"] not in ids:
            ids.insert(0, attrs["option_id"])
        if not ids:
            raise serializers.ValidationError({"option_ids": "Pick at least one option."})
        attrs["option_ids"] = ids
        return attrs


class PostAnswerSerializer(serializers.ModelSerializer):
    """TASK G6 — one answer on a 'doubt' (Ask-a-doubt) post."""

    user = serializers.SerializerMethodField()
    is_own_answer = serializers.SerializerMethodField()
    # Doubt post upgrade: step-wise solution + pictures / video. Both are [] for a plain text answer.
    steps = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()

    class Meta:
        model = PostAnswer
        fields = [
            "id", "user", "content", "is_best_answer", "likes_count",
            "created_at", "is_own_answer", "steps", "attachments",
        ]
        read_only_fields = fields

    def get_steps(self, obj):
        request = self.context.get("request")
        # `.all()` reads the prefetch (list endpoint / feed page) and falls back to one query for a single answer
        return [
            {"position": s.position, "text": s.text, "image": doubts._file_url(s.image, request)}
            for s in obj.steps.all()
        ]

    def get_attachments(self, obj):
        request = self.context.get("request")
        return [
            {"id": a.pk, "kind": a.kind, "url": doubts._file_url(a.file, request), "size": a.file_size_bytes}
            for a in obj.attachments.all()
        ]

    def get_user(self, obj):
        request = self.context.get("request")
        from .trust_signals import author_trust  # H15: teacher / verified badge on the answerer (no query)

        return {
            "id": str(obj.user.id),
            "username": obj.user.username,
            "profile_picture": get_profile_pic_url(obj.user, request),
            **author_trust(obj.user),
        }

    def get_is_own_answer(self, obj):
        request = self.context.get("request")
        return bool(
            request and request.user.is_authenticated and request.user.id == obj.user_id
        )


class PostAnswerCreateSerializer(serializers.Serializer):
    """TASK G6 — body of POST /post/<post_id>/answers/ (JSON or multipart).

    Doubt post upgrade: `content` is optional when the answer is a step-wise solution or carries pictures / a video.
        content        text (max 5000)
        steps          JSON list [{"text": "..."}], max 20; the picture of step i is the file `step_image_<i>`
        answer_images  up to 4 pictures            answer_video  one short video
    Everything is validated by post/doubts.clean_answer_input; `validated_data` is its cleaned dict."""

    content = serializers.CharField(max_length=5000, required=False, allow_blank=True)

    def validate(self, attrs):
        request = self.context.get("request")
        files = getattr(request, "FILES", None)
        return doubts.clean_answer_input(self.initial_data, files)


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


class _ScheduleDateTimeField(serializers.DateTimeField):
    """Round 5: the app sends `scheduled_at` in UTC ("...Z"). An older client may send a naive local time together with
    `scheduled_timezone` ("IST (UTC+05:30)"): then that offset is used, so the post does not go live hours early / late
    just because the server guessed its own zone. No label -> DRF's default (server zone), as before."""

    def enforce_timezone(self, value):
        initial = getattr(getattr(self, "parent", None), "initial_data", None)
        label = initial.get("scheduled_timezone") if hasattr(initial, "get") else None
        if timezone.is_naive(value) and isinstance(label, str):
            from .post_options import zone_from_label
            tz = zone_from_label(label)
            if tz is not None:
                return value.replace(tzinfo=tz)
        return super().enforce_timezone(value)


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
    # P1: the app sends hashtags as ONE jsonEncode()d multipart field (same as media_types), so
    # a plain ListField saw a one-element list holding the whole JSON string and the P1
    # normaliser then dropped it as an invalid tag. Decode it like the other list fields.
    hashtags = _JSONEncodedListField(child=serializers.CharField(max_length=100), required=False, allow_empty=True)

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
    # P1 - accessibility text per attachment, same order as `media_files` (JSON list).
    media_alt_texts = _JSONEncodedListField(
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
    # Poll/quiz settings (JSON object): duration_hours (1-168), allow_multiple, max_choices, is_anonymous,
    # results_visibility (always|after_vote|after_end), is_quiz, explanation. Per-option `is_correct` rides inside
    # `poll_options`; option images are files named poll_option_image_<index>. Absent = legacy single-choice poll.
    poll_settings = _JSONEncodedDictField(write_only=True, required=False, child=serializers.JSONField())
    # Doubt post upgrade (post_type "doubt" only): {"subject", "class_level", "exam", "chapter", "is_urgent",
    # "ocr_text" (text the phone read from the question photo), "bounty_coins" (0 or 5-500, held from the wallet)}.
    # Rules live in post/doubts.clean_doubt_settings. Multipart sends it as one JSON-encoded form field.
    doubt_settings = _JSONEncodedDictField(write_only=True, required=False, child=serializers.JSONField())
    # P5a: people tagged in the post [{"user_id": 5, "x": 0.3, "y": 0.4}] (x/y optional, both or neither).
    tags = _JSONEncodedListField(child=serializers.DictField(), write_only=True, required=False, allow_empty=True)
    # `source="published_at"` reuses the column that already existed for
    # exactly this ("For scheduled posts") instead of adding a second,
    # redundant datetime column with a different name.
    scheduled_at = _ScheduleDateTimeField(source="published_at", required=False, allow_null=True)
    # Round 5: people invited to co-author the post (user ids, jsonEncode()d in multipart). They are invited after the
    # post exists (post/collaborators.py); a failed invite never fails the post.
    collaborator_ids = _JSONEncodedListField(child=serializers.IntegerField(), write_only=True, required=False, allow_empty=True)

    def validate_scheduled_timezone(self, value):
        from .post_options import scheduled_zone_label
        return scheduled_zone_label(value)

    class Meta:
        model = Post
        fields = [
            "id", "title", "content", "category", "subcategory", "post_type", "visibility",
            "hashtags", "mentioned_user_ids", "metadata", "location", "media_files", "media_types",
            "media_captions", "media_alt_texts", "poll_options", "poll_settings", "scheduled_at", "created_at", "tags",
            "doubt_settings",
            # Round 5 - post options: who may comment / comments off / downloads / schedule zone label / co-authors.
            "comments_limit", "is_comments_disabled", "download_allowed", "scheduled_timezone", "collaborator_ids",
            # S4b - may others repost / save this post (account-level default when the composer sends nothing)
            "allow_reshare", "allow_save",
            # P1 — what the composer's type chip created (resource posts use
            # POST /post/resources/ instead, which also takes the files).
            "kind", "topics",
        ]
        read_only_fields = ["id", "created_at"]

    def validate_kind(self, value):
        if value == Post.KIND_RESOURCE:
            raise serializers.ValidationError(
                "Resource posts are created with POST /post/resources/ (they carry files).")
        return value

    def validate_topics(self, value):
        from .hashtags import clean_topics
        return clean_topics(value)

    def validate_mentioned_user_ids(self, ids):
        """Can't tag / @mention someone in a block relationship with the
        author (either direction). Dropped silently so a block isn't revealed."""
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not ids or user is None or not getattr(user, "is_authenticated", False):
            return ids
        from user_profile.services import blocked_user_ids

        blocked = {str(i) for i in blocked_user_ids(user)}
        ids = [i for i in ids if str(i) not in blocked]
        # S4: people who limited "who can mention me" are dropped silently too.
        from user_profile import privacy as privacy_rules

        allowed = privacy_rules.allowed_owner_ids(user, ids, "mention_from")
        return [i for i in ids if privacy_rules.as_user_id(i) in allowed]

    def validate_media_files(self, files):
        # 🔥 FIX: this is the hook that was missing entirely — extension/size
        # now actually get checked before any file touches disk.
        if len(files) > 10:
            raise serializers.ValidationError("A post can have at most 10 media files.")
        for f in files:
            validate_media_file(f)
        return files

    def validate_poll_options(self, options):
        # Shape only. The real rules (2-6 options, text OR image, no duplicates, quiz answers, settings) need the
        # uploaded option images too, so they run in validate() -> polls.clean_poll_payload(). The client check is
        # a UX nicety, not a security boundary; the API holds the line for any caller.
        if len(options) > PostPoll.MAX_OPTIONS:
            raise serializers.ValidationError(f"A poll can have at most {PostPoll.MAX_OPTIONS} options.")
        return options

    def validate_post_type(self, value):
        # `repost` is a valid Post.post_type now, but a repost only makes
        # sense with an `original_post` — that's set exclusively by
        # POST /post/<id>/repost/ (PostRepostAPIView), never by this
        # serializer, which has no original_post field at all.
        if value == "repost":
            raise serializers.ValidationError("Use POST /post/<id>/repost/ to repost.")
        return value

    def to_internal_value(self, data):
        # T3.3 - the composer sends post_type "mixed" when one post carries different kinds of files (photo + video + PDF).
        # It is not a Post.post_type choice, so the ChoiceField used to answer 400 "not a valid choice" and the post was
        # lost. Same data, valid value: a multi-kind post is a "carousel".
        if hasattr(data, "get") and str(data.get("post_type") or "").strip().lower() == "mixed":
            import copy as _copy

            data = _copy.copy(data)  # shallow: QueryDict.copy() would deep-copy the uploaded files
            data["post_type"] = "carousel"
        return super().to_internal_value(data)

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

    def validate_content(self, value):
        # H-rich-caption: strip HTML / control / bidi characters, cap the length (post/rich_text.py)
        from .rich_text import clean_content
        return clean_content(value)

    def validate_title(self, value):
        from .rich_text import clean_title
        return clean_title(value) or None

    def validate(self, attrs):
        # S4b: whatever the composer did NOT send starts from the author's account-level defaults (audience, who may
        # comment, allow reshare / save). Decided from the RAW data: a multipart form that leaves a switch out would
        # otherwise read as "off". A value that was sent is never touched.
        from .content_defaults import post_fill
        attrs.update(post_fill(self.context["request"].user, getattr(self, "initial_data", None)))
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
        # Doubt post upgrade: structured fields / urgent / bounty, question photos only.
        if post_type == "doubt":
            doubts.validate_question_photos(media_files)
            attrs["doubt_settings"] = doubts.clean_doubt_settings(
                attrs.get("doubt_settings"), self.context["request"].user)
        elif attrs.get("doubt_settings"):
            raise serializers.ValidationError({"doubt_settings": "doubt_settings sirf doubt post ke liye hai."})
        if post_type in ["image", "video", "document"] and not media_files:
            raise serializers.ValidationError({"media_files": f"{post_type} post me file required hai"})

        # A poll needs its options regardless of what post_type string the
        # client sent — new_post.dart lets a poll ride along on a
        # text/image/video post (`_hasPoll` is independent of `_postType`
        # in the composer), so gate on "poll_options were actually sent",
        # not on `post_type == "poll"`.
        if post_type == "poll" and not poll_options:
            raise serializers.ValidationError({"poll_options": "Poll post me poll_options required hai"})
        if poll_options is not None:
            cleaned_options, cleaned_settings = polls.clean_poll_payload(
                poll_options, attrs.get("poll_settings"), self.context["request"].FILES)
            attrs["poll_options"] = cleaned_options
            attrs["poll_settings"] = cleaned_settings
        elif "poll_settings" in attrs:
            raise serializers.ValidationError({"poll_settings": "poll_settings needs poll_options."})

        if subcategory and not category:
            raise serializers.ValidationError({"subcategory": "Subcategory needs a category selected first."})

        # Reel studio: `metadata.reel` is re-checked here (unknown keys dropped, music licence / remix source verified);
        # a `reel` block on a non-video post is removed. Rules: post/reel_meta.py
        if isinstance(attrs.get("metadata"), (dict, str)):
            from .reel_meta import ReelMetaError, clean_post_metadata
            try:
                attrs["metadata"] = clean_post_metadata(attrs["metadata"], post_type, self.context["request"].user)
            except ReelMetaError as exc:
                raise serializers.ValidationError({"metadata": str(exc)})

        if "tags" in attrs:
            from .tagging import TagError, parse_tags
            try:
                attrs["tags"] = parse_tags(attrs["tags"], self.context["request"].user, attrs.get("visibility", "public"))
            except TagError as exc:
                raise serializers.ValidationError({"tags": str(exc)})

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
        media_alt_texts = validated_data.pop("media_alt_texts", [])
        poll_options = validated_data.pop("poll_options", None)
        poll_settings = validated_data.pop("poll_settings", None)
        tags = validated_data.pop("tags", None)
        doubt_settings = validated_data.pop("doubt_settings", None)
        collaborator_ids = validated_data.pop("collaborator_ids", None)
        validated_data.pop("media_files", None)
        if not validated_data.get("published_at"):
            validated_data.pop("scheduled_timezone", None)  # a zone label only means something with a schedule

        # `scheduled_at` -> `published_at` (see the field's `source=`).
        # Presence of a (future — `validate()` already checked this) value
        # is what "this post is scheduled" means; no separate client-sent
        # boolean is trusted for it.
        validated_data["is_scheduled"] = bool(validated_data.get("published_at"))

        content = validated_data.get("content") or ""
        # T3.1 - title is OPTIONAL on a media / file post: empty -> text (50 chars) -> first file name -> "Post - <date>".
        # Text / poll / doubt posts keep no title (their text IS the post, a copy of it as a title would show twice).
        if not validated_data.get("title") and media_files and validated_data.get("post_type") not in ("doubt", "poll", "repost", "text"):
            from .auto_title import derive_title
            validated_data["title"] = derive_title(content, [getattr(f, "name", "") for f in media_files])
        # P1: chips chosen in the composer + #tags typed in the text are merged
        # (normalise_list below de-duplicates and caps at MAX_HASHTAGS_PER_POST).
        if content:
            from .hashtags import extract_from_text
            validated_data["hashtags"] = list(validated_data.get("hashtags") or []) + extract_from_text(content)

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
            # P1: NFKC + casefold (unicode safe), invalid tags dropped, duplicates
            # removed, hard cap of MAX_HASHTAGS_PER_POST (10).
            from .hashtags import normalize_list
            validated_data["hashtags"] = normalize_list(validated_data["hashtags"])

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
            alt_text = (media_alt_texts[idx] if idx < len(media_alt_texts) else "") or ""
            PostMedia.objects.create(
                post=post, media_type=media_type, file=file, file_name=file.name,
                file_size_bytes=file.size, mime_type=file.content_type or "",
                display_order=idx, caption=caption, alt_text=alt_text.strip()[:500],
            )

        # Reel composer: optional custom cover frame -> thumbnail of the first video.
        reel_cover = request.FILES.get("reel_cover")
        if reel_cover is not None and media_files:
            from .services import attach_reel_cover
            attach_reel_cover(post, reel_cover)

        # TASK 5 — persist the poll. `validate_poll_options()` already
        # confirmed 2-4 non-empty, non-duplicate options by this point;
        # `votes_count`/`total_votes_count` are never taken from the
        # client (`pollData` sends `votes: 0` per option, which is simply
        # ignored) — they're server-owned counters, kept accurate by
        # `sync_poll_vote_counts` in models.py once a voting endpoint
        # exists to create `PostPollVote` rows.
        if poll_options:
            # `poll_options` / `poll_settings` were cleaned by validate() (polls.clean_poll_payload):
            # counters are never taken from the client, only server-derived.
            polls.create_poll(post, poll_options, poll_settings)

        # 🔥 FIX: this used to call a `services.attach_hashtags()` that
        # assumed a separate `Hashtag`/`PostHashtag` model — neither
        # exists. `hashtags` is a plain `Post.hashtags` JSONField, and it
        # was already saved on the row by `Post.objects.create(**validated_data)`
        # above (it's part of `validated_data`, either passed in explicitly
        # or auto-extracted from `#tags` in content a few lines up). There
        # is nothing left to do here — see services.py's module docstring
        # for the full story on why that call was dead/broken.

        from .hashtags import sync_post_hashtags
        sync_post_hashtags(post)

        if post.post_type == "doubt":
            # PostDoubt row (+ the coin bounty hold). Teachers of the subject are told by post.signals once the post
            # is visible (approved + not scheduled), or later when it goes live / is released from a hold.
            doubts.create_doubt(post, doubt_settings)

        if tags:
            from .tagging import notify_post_tags_on_commit, sync_post_tags
            sync_post_tags(post, tags)
            notify_post_tags_on_commit(post)  # no-op for a scheduled post; the publisher notifies later
        if collaborator_ids:
            from . import collaborators as _collab
            try:
                _collab.invite(post, request.user, collaborator_ids)
            except _collab.CollabError:
                pass  # best effort: the post is already created; the author can invite again from the post menu
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
            "scheduled_timezone": instance.scheduled_timezone,
            "comments_limit": instance.comments_limit,
            "is_comments_disabled": instance.is_comments_disabled,
            "download_allowed": instance.download_allowed,
            "allow_reshare": instance.allow_reshare,
            "allow_save": instance.allow_save,
            "created_at": instance.created_at,
            "media": PostMediaSerializer(instance.media.all(), many=True, context={"request": request}).data,
            "poll": (
                PostPollSerializer(instance.poll, context={"request": request}).data
                if hasattr(instance, "poll") else None
            ),
            "tagged_users": tagged_users_payload(instance, request),
            "doubt": doubts.doubt_payload(instance) if instance.post_type == "doubt" else None,
        }


class PostAttachmentSerializer(serializers.ModelSerializer):
    """One file of a resource post. `url` / `download_url` are only present
    when the viewer may open / download it (a signed /pmedia/ link, minted here,
    so it only ever exists inside a response the permission layer authorised)."""
    name = serializers.CharField(source="original_name", read_only=True)
    thumbnail_url = serializers.SerializerMethodField()
    url = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    can_download = serializers.SerializerMethodField()
    has_text = serializers.SerializerMethodField()
    watermark_text = serializers.SerializerMethodField()

    class Meta:
        model = PostAttachment
        fields = ["id", "kind", "name", "mime", "size", "pages", "alt_text", "order",
                  "thumbnail_url", "url", "download_url", "can_download",
                  "version", "version_note", "has_text", "text_source", "watermark_text"]

    def get_has_text(self, obj):
        return bool(obj.search_text)

    def get_watermark_text(self, obj):
        from .resource_watermark import watermark_text
        return watermark_text(obj.post.watermark_mode, obj.post, self._viewer())

    def _viewer(self):
        req = self.context.get("request")
        return getattr(req, "user", None)

    def get_can_download(self, obj):
        v = self._viewer()
        return bool(v and (obj.post.user_id == v.id or obj.post.download_allowed))

    def get_thumbnail_url(self, obj):
        return obj.thumbnail.url if obj.thumbnail else None

    def get_url(self, obj):
        # In-app viewer link (always allowed once the post itself is visible).
        return obj.file.url if obj.file else None

    def get_download_url(self, obj):
        if not self.get_can_download(obj):
            return None
        req = self.context.get("request")
        from django.urls import reverse
        path = reverse("post-attachment-download", kwargs={"attachment_id": obj.id})
        return req.build_absolute_uri(path) if req else path


class ViewerStateListSerializer(serializers.ListSerializer):
    """Batch the per-post VIEWER state for a whole page (fixes the feed N+1).

    Before: every post in a list cost ~5 extra queries (is_liked, is_saved, my_reaction, reposted-by-me,
    poll) -> a 25-post feed page was ~150 queries and grew linearly with page size. The per-object
    serializer methods already honour `is_liked_annotated` / `is_saved_annotated` /
    `is_reposted_annotated`, but NO view ever set them. Doing it here (instead of in every view) fixes
    every list endpoint that uses `PostListSerializer(many=True)`: home feed, explore, profile, saved...

    Now: 4 queries per page, independent of page size.
    """

    def to_representation(self, data):
        items = list(data.all() if hasattr(data, "all") else data)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if items and user is not None and user.is_authenticated and not self.context.get("_repost_nested"):
            self._stamp(items, user, request)
        return super().to_representation(items)

    @staticmethod
    def _stamp_repost_originals(items, user, request=None):
        """P-C5: batch everything `get_original_post` needs for the whole page.

        Before: per repost card ~6+ queries (original.user, post_tags, collaborators, attachments/paper, poll,
        viewer reaction/save/repost flags) + one Follow lookup when the original's author is private.
        Now: a constant number of queries per page, independent of how many repost cards the page has.
        Returns the stamped original posts (never reposts themselves - the chain is flattened at repost time)."""
        from django.db.models import prefetch_related_objects

        reposts = [p for p in items if p.post_type == "repost" and p.original_post_id]
        if not reposts:
            return []
        # Already-cached FKs (select_related by the view) are skipped by Django; only missing ones cost a query.
        prefetch_related_objects(reposts, "original_post__user")
        seen, originals = set(), []
        for p in reposts:
            o = p.original_post
            if o is None or o.pk in seen or o.post_type == "repost":
                continue  # SET_NULL original, duplicate card, or a chain that slipped in (stays un-nested)
            seen.add(o.pk)
            originals.append(o)
        if not originals:
            return []
        # Private-author check of get_original_post: one Follow query for the whole page instead of one per card.
        private_ids = {o.user_id for o in originals if o.user.is_private and o.user_id != user.id}
        following = set()
        if private_ids:
            from user_profile.models import Follow

            following = set(Follow.objects.filter(
                follower_id=user.id, following_id__in=private_ids, status=Follow.Status.ACCEPTED,
            ).values_list("following_id", flat=True))
        for o in originals:
            o._viewer_follows_author = o.user_id in following
        ViewerStateListSerializer._stamp(originals, user, request)  # originals are not reposts -> no further recursion
        return originals

    @staticmethod
    def _stamp(items, user, request=None):
        from django.db.models import prefetch_related_objects

        # P-C5: repost cards embed their ORIGINAL post (PostListSerializer, one level). Stamp the originals the same
        # way as the page itself, so the nested serializer finds everything prefetched instead of querying per repost.
        originals = ViewerStateListSerializer._stamp_repost_originals(items, user, request)

        ids = [p.pk for p in items]
        reactions = dict(PostLike.objects.filter(user=user, post_id__in=ids).values_list("post_id", "reaction_type"))
        saved = set(PostSave.objects.filter(user=user, post_id__in=ids).values_list("post_id", flat=True))
        reposted = set(Post.objects.filter(
            user=user, original_post_id__in=ids, is_deleted=False).values_list("original_post_id", flat=True))
        for p in items:
            p.is_liked_annotated = p.pk in reactions
            p.is_saved_annotated = p.pk in saved
            p.is_reposted_annotated = p.pk in reposted
            p.my_reaction_annotated = reactions.get(p.pk)
        # Reverse OneToOne: one query for all polls instead of one per post (absent poll -> DoesNotExist,
        # which get_poll already handles).
        prefetch_related_objects([p for p in items if p.post_type != "repost"], "poll")
        polls.stamp_polls([p for p in items if p.post_type != "repost"], user)  # options + my votes: 2 queries per page
        prefetch_related_objects(
            [p for p in items if p.post_type != "repost"],
            Prefetch("post_tags", queryset=PostTag.objects.select_related("tagged_user")),
            Prefetch("collaborators", queryset=PostCollaborator.objects.select_related("user")),
        )
        # H2: Notes / Resource posts read `obj.attachments.all()` (get_attachments) - one query PER such post
        # on every feed page. The reverse-FK prefetch also fills `attachment.post`, which can_download reads.
        prefetch_related_objects([p for p in items if p.kind == "resource"], "attachments", "paper")
        # H2: doubt posts ran one `answers.filter(is_best_answer=True).first()` EACH; shared class / test-series
        # cards ran 2+ queries EACH. One query per kind for the whole page; get_best_answer / get_shared_item read these.
        doubt_posts = [p for p in items if p.post_type == "doubt"]
        if doubt_posts:
            best = {}
            for a in (PostAnswer.objects.filter(post_id__in=[p.pk for p in doubt_posts], is_best_answer=True)
                      .select_related("user").prefetch_related("steps", "attachments")):
                best.setdefault(a.post_id, a)  # Meta.ordering = best first, then most liked, then oldest -> first row wins
            for p in doubt_posts:
                p.best_answer_prefetched = best.get(p.pk)
            # Doubt post upgrade: subject / urgent / solved / bounty block, one query for the page
            prefetch_related_objects(doubt_posts, "doubt_info")
        # H15: "helpful for N students" (resource posts) + "answered by teacher" (doubts): one grouped query each,
        # only when the page has such posts.
        from .trust_signals import stamp_trust

        stamp_trust(items, user)
        shared = [p for p in items if p.shared_type]
        if shared:
            from .shared_items import resolve_shared_items_bulk

            cards = resolve_shared_items_bulk(shared, request)
            for p in shared:
                if p.pk in cards:
                    p.shared_item_prefetched = cards[p.pk]


class PostListSerializer(serializers.ModelSerializer):
    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get("location"):  # H23: an account that is a minor never shows a location, old posts included
            from .minor_privacy import hide_location_for_author
            if hide_location_for_author(instance.user):
                data["location"] = {}
        return data

    user = serializers.SerializerMethodField()
    media = PostMediaSerializer(many=True, read_only=True)
    is_liked = serializers.SerializerMethodField()
    is_saved = serializers.SerializerMethodField()
    # S4b: may THIS viewer repost / send / save the post? (the author is never limited; `allow_*` are the raw switches)
    can_reshare = serializers.SerializerMethodField()
    can_save = serializers.SerializerMethodField()
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
    # Doubt post upgrade: {subject, class_level, exam, chapter, is_urgent, is_solved, solved_at, bounty_coins,
    # bounty_status} for a doubt post, None for every other post.
    doubt = serializers.SerializerMethodField()
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
    is_exploration = serializers.SerializerMethodField()
    shared_item = serializers.SerializerMethodField()
    # P1 — resource posts: files (empty list for every other kind).
    attachments = serializers.SerializerMethodField()
    # Resources upgrade: research-paper metadata + citations (None when the post has none).
    paper = serializers.SerializerMethodField()
    # P5a — people tagged in this post (x/y only when placed on the first photo).
    tagged_users = serializers.SerializerMethodField()
    collaborators = serializers.SerializerMethodField()
    # H15 — trust / quality signals (post/trust_signals.py). Batched per page by ViewerStateListSerializer._stamp.
    helpful_count = serializers.SerializerMethodField()
    is_helpful_by_me = serializers.SerializerMethodField()
    answered_by_teacher = serializers.SerializerMethodField()
    # R-B4 - "Part 2/5" / "N chapters" chip on a feed video card: only {series, chapters} of metadata.reel, None for
    # every other post. No query (reads the loaded metadata column), and the rest of `metadata` never goes to the feed.
    reel_study = serializers.SerializerMethodField()

    class Meta:
        list_serializer_class = ViewerStateListSerializer
        model = Post
        fields = [
            "id", "user", "title", "content", "category", "subcategory", "post_type",
            "visibility", "hashtags", "location", "likes_count",
            "comments_count", "shares_count", "views_count", "saves_count",
            "is_liked", "is_saved", "my_reaction", "poll",
            # TASK G6 — "Ask a doubt" post type.
            "answers_count", "best_answer", "doubt",
            "like_count", "confuse_count", "wrong_count", "imp_count", "explain_count",
            "is_scheduled", "scheduled_at", "created_at", "media",
            # Round 5 - post options: "Edited" time, who may comment, the schedule's zone label, co-authors.
            "edited_at", "comments_limit", "is_comments_disabled", "scheduled_timezone", "collaborators",
            "allow_reshare", "allow_save", "can_reshare", "can_save",  # S4b
            "original_post", "repost_caption", "reposts_count", "is_reposted_by_me",
            "shared_item",
            # NEW — surfaces PostEditAPIView's edits (see PostEditSerializer
            # below). Field already existed on the model; nothing exposed it.
            "is_edited", "feed_source", "is_exploration",
            # P1 — composer type + resource fields.
            "kind", "subject", "level", "topics", "download_allowed", "attachments",
            "watermark_mode", "paper",
            # P3 — pinned on the author's profile grid; P5a — tagged people.
            "is_pinned", "is_archived", "tagged_users",
            # H15 — "helpful for N students" (resource), "answered by teacher" (doubt); author badge is in `user`.
            "helpful_count", "is_helpful_by_me", "answered_by_teacher",
            # R-B4 - series part / chapters chip of a video post (reel studio info).
            "reel_study",
        ]

    def _trust_viewer(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def get_helpful_count(self, obj):
        if self.context.get("_repost_nested") or getattr(obj, "kind", "") != "resource":
            return 0
        from .trust_signals import helpful_for

        return helpful_for(obj, self._trust_viewer())[0]

    def get_is_helpful_by_me(self, obj):
        if self.context.get("_repost_nested") or getattr(obj, "kind", "") != "resource":
            return False
        from .trust_signals import helpful_for

        return helpful_for(obj, self._trust_viewer())[1]

    def get_answered_by_teacher(self, obj):
        if self.context.get("_repost_nested") or getattr(obj, "post_type", "") != "doubt":
            return False
        from .trust_signals import teacher_answered_for

        return teacher_answered_for(obj)

    def get_reel_study(self, obj):
        if getattr(obj, "post_type", "") != "video":
            return None
        from .reel_meta import feed_study_chip

        return feed_study_chip(obj)

    def get_tagged_users(self, obj):
        if getattr(obj, "post_type", "") == "repost":
            return []
        return tagged_users_payload(obj, self.context.get("request"))

    def get_paper(self, obj):
        if getattr(obj, "kind", "post") != "resource" or self.context.get("_repost_nested"):
            return None
        from .resource_views import paper_payload
        return paper_payload(obj)

    def get_attachments(self, obj):
        if getattr(obj, "kind", "post") != "resource":
            return []
        return PostAttachmentSerializer(
            obj.attachments.all(), many=True, context=self.context).data

    def get_shared_item(self, obj):
        # Embedded tuition-class / test-series card (None on ordinary posts).
        if not obj.shared_type or self.context.get("_repost_nested"):
            return None
        from .shared_items import resolve_shared_item

        if getattr(obj, "shared_item_prefetched", None) is not None:  # batched by ViewerStateListSerializer._stamp
            return obj.shared_item_prefetched
        return resolve_shared_item(
            obj.shared_type, obj.shared_id, obj.user_id, request=self.context.get("request"),
        )

    def get_feed_source(self, obj):
        return (self.context.get("feed_sources") or {}).get(obj.id)

    def get_is_exploration(self, obj):
        # T1 Part 3: True when this card is an exploration slot (new creator /
        # test audience). Additive field; False everywhere outside Home.
        return obj.id in (self.context.get("explore_ids") or ())

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

        from .trust_signals import author_trust  # H15: verified / teacher badge (read off the loaded author row)

        return {
            "id": str(obj.user.id),
            "username": obj.user.username,
            "profile_picture": get_profile_pic_url(obj.user, request),
            "is_following": is_following,
            "is_own_post": is_own_post,
            **author_trust(obj.user),
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

    def get_can_reshare(self, obj):
        from .post_options import can_reshare_post
        return can_reshare_post(obj, getattr(self.context.get("request"), "user", None))

    def get_can_save(self, obj):
        from .post_options import can_save_post
        return can_save_post(obj, getattr(self.context.get("request"), "user", None))

    def get_my_reaction(self, obj):
        request = self.context.get("request")
        if self.context.get("_repost_nested"):
            return None
        if request and request.user.is_authenticated:
            if hasattr(obj, "my_reaction_annotated"):  # stamped by ViewerStateListSerializer
                return obj.my_reaction_annotated
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
        if hasattr(obj, "best_answer_prefetched"):  # batched by ViewerStateListSerializer._stamp (None = no best answer)
            best = obj.best_answer_prefetched
        else:
            best = obj.answers.filter(is_best_answer=True).prefetch_related("steps", "attachments").first()
        return PostAnswerSerializer(best, context={"request": request}).data if best else None

    def get_doubt(self, obj):
        if obj.post_type != "doubt" or self.context.get("_repost_nested"):
            return None
        if hasattr(obj, "best_answer_prefetched"):
            best_exists = obj.best_answer_prefetched is not None
        else:
            best_exists = obj.answers.filter(is_best_answer=True).exists()
        return doubts.doubt_payload(obj, best_exists=best_exists)

    def get_collaborators(self, obj):
        """Accepted co-authors ("with @x"); prefetched with the page (see the `collaborators` Prefetch below)."""
        if obj.post_type == "repost":
            return []
        from .collaborators import payload
        return payload(obj, self.context.get("request"))

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

            batched = getattr(original, "_viewer_follows_author", None)  # P-C5: stamped per page by _stamp_repost_originals
            if batched is not None:
                unavailable = not (viewer_id and batched)
            else:
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


def hls_playlist_url(request, media):
    """Absolute URL of the HLS master playlist of a video PostMedia, or None (no HLS yet / not a video).
    Segments and variant playlists are referenced RELATIVELY inside the playlist, so they resolve next to it."""
    name = getattr(media, "hls_playlist", "") or ""
    if not name or media.media_type != "video":
        return None
    try:
        url = PostMedia._meta.get_field("file").storage.url(name)
    except Exception:
        return None
    return request.build_absolute_uri(url) if request is not None else url


def hls_low_url(request, media):
    """P-E2: absolute URL of the LOWEST rung's playlist of a video PostMedia (`<master dir>/<rung>/index.m3u8`), or None
    (no HLS yet / old row that does not remember its rungs). The app plays it first for a quick first frame, then upgrades."""
    master = getattr(media, "hls_playlist", "") or ""
    if not master or media.media_type != "video" or "/" not in master:
        return None
    from .services import lowest_hls_rung_name

    rung = lowest_hls_rung_name(media)
    if not rung:
        return None
    try:
        url = PostMedia._meta.get_field("file").storage.url(master.rsplit("/", 1)[0] + "/" + rung + "/index.m3u8")
    except Exception:
        return None
    return request.build_absolute_uri(url) if request is not None else url


def video_sprite_payload(request, media):
    """Seek-bar scrub preview of a video PostMedia, or None (not a video / no sprite built yet / disabled).
    {url, interval, frames, cols, rows, thumb_w, thumb_h}: tile i sits at column i % cols, row i // cols and covers
    the second i * interval of the video. Stored in `metadata["sprite"]` (see post.tasks._attach_sprite), so no column."""
    if getattr(media, "media_type", None) != "video":
        return None
    from .services import sprite_info

    info = sprite_info(media)
    if info is None:
        return None
    try:
        url = PostMedia._meta.get_field("file").storage.url(info["name"])
    except Exception:
        return None
    out = {k: v for k, v in info.items() if k != "name"}
    out["url"] = request.build_absolute_uri(url) if request is not None else url
    return out


def reel_captions_payload(post):
    """{language, cues: [{s, e, t}]} stored by the reel studio in `Post.metadata["reel"]["captions"]` (already
    sanitized by post.reel_meta when the reel was created), or None. No query: reads the loaded post."""
    meta = post.metadata if isinstance(getattr(post, "metadata", None), dict) else {}
    reel = meta.get("reel") if isinstance(meta.get("reel"), dict) else {}
    caps = reel.get("captions")
    if not isinstance(caps, dict) or not isinstance(caps.get("cues"), list) or not caps["cues"]:
        return None
    return {"language": str(caps.get("language") or ""), "cues": caps["cues"]}


def _absolute_file_url(request, file_field):
    if not file_field:
        return None
    try:
        url = file_field.url
    except ValueError:
        return None
    return request.build_absolute_uri(url) if request is not None else url


def reel_caption_tracks_payload(request, media):
    """R-B3: the READY subtitle tracks of the reel's video (VideoCaptionTrack: the lecture auto-captions made by
    speech-to-text, or the author's own .vtt) as `[{language, source, url}]`, sorted by language. The reel studio's own
    CC track (`video.captions`) stays first; the app falls back to these when a reel has no studio captions.

    Reads ONLY the prefetched `media.caption_tracks` (`reels.page_queryset` prefetches it): a caller that did not
    prefetch gets `[]` and NO query, so no serializer user can turn this into an N+1."""
    cache = getattr(media, "_prefetched_objects_cache", None) or {}
    if "caption_tracks" not in cache:
        return []
    out = []
    for track in media.caption_tracks.all():
        if track.status != "ready" or not track.file:
            continue
        url = _absolute_file_url(request, track.file)
        if url:
            out.append({"language": track.language, "source": track.source, "url": url})
    out.sort(key=lambda t: t["language"])
    return out


class ReelSerializer(serializers.ModelSerializer):
    video = serializers.SerializerMethodField()
    caption = serializers.SerializerMethodField()
    author = serializers.SerializerMethodField()
    counts = serializers.SerializerMethodField()
    is_liked = serializers.SerializerMethodField()
    my_reaction = serializers.SerializerMethodField()
    is_saved = serializers.SerializerMethodField()
    can_reshare = serializers.SerializerMethodField()  # S4b
    can_save = serializers.SerializerMethodField()  # S4b
    feed_source = serializers.SerializerMethodField()
    study = serializers.SerializerMethodField()
    sound = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            "id", "video", "caption", "hashtags", "author", "counts",
            "is_liked", "my_reaction", "is_saved", "feed_source", "study", "sound",
            # X3: who may comment (the Reels comment sheet shows the "comments off" banner / followers note)
            "comments_limit", "is_comments_disabled",
            # S4b: the author may switch resharing / saving off
            "allow_reshare", "allow_save", "can_reshare", "can_save",
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
            "media_id": str(media.id),
            "processing_status": media.processing_status,  # "ready" | "pending" | "processing" | "failed"
            "url": _absolute_file_url(request, media.file),
            "hls_url": hls_playlist_url(request, media),  # adaptive stream; `url` (MP4) stays the fallback
            "hls_low_url": hls_low_url(request, media),  # P-E2: lowest rung (quick first frame); null = old row / no HLS
            "sprite": video_sprite_payload(request, media),  # seek-bar scrub preview; null until built
            "captions": reel_captions_payload(obj),  # CC track from the reel studio; null when the reel has none
            "caption_tracks": reel_caption_tracks_payload(request, media),  # R-B3: lecture auto / uploaded subtitles (WebVTT urls)
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

    def get_can_reshare(self, obj):  # S4b
        from .post_options import can_reshare_post
        return can_reshare_post(obj, self._viewer())

    def get_can_save(self, obj):  # S4b
        from .post_options import can_save_post
        return can_save_post(obj, self._viewer())

    def get_feed_source(self, obj):
        return (self.context.get("feed_sources") or {}).get(obj.id)

    def get_study(self, obj):
        # X2: series / chapters / subject / exams / notes / test of the reel studio (null when the reel has none).
        from .reel_meta import study_payload

        return study_payload(obj)

    def get_sound(self, obj):
        # X7: sound credit (CC0 sound / own audio / original audio; "removed" after a takedown). No query.
        from .sounds import credit_for

        return credit_for(obj)


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
    # P1: the "Topics" chips. Needed by the app after a chunked (large video) upload, because the
    # chunked init endpoint has no topics field. Free text, cleaned like on create.
    topics = serializers.ListField(child=serializers.CharField(max_length=100, allow_blank=True), required=False, allow_empty=True)
    # P5a: replaces the whole tag set ([] clears it). Tags are metadata: a tags-only edit never sets is_edited.
    tags = serializers.ListField(child=serializers.DictField(), required=False, allow_empty=True, write_only=True)
    # Round 5: the audience can be changed in the same edit; the extra settings and co-author invites too.
    visibility = serializers.ChoiceField(choices=Post.VISIBILITY_CHOICES, required=False)
    collaborator_ids = serializers.ListField(child=serializers.IntegerField(), required=False, allow_empty=True, write_only=True)

    # Settings that never stamp the "Edited" mark (they are not text the viewers read).
    META_ONLY = {"topics", "visibility", "comments_limit", "is_comments_disabled", "download_allowed",
                 "allow_reshare", "allow_save"}

    class Meta:
        model = Post
        fields = ["title", "content", "category", "subcategory", "hashtags", "topics", "mentioned_user_ids", "metadata", "location", "tags",
                  "visibility", "comments_limit", "is_comments_disabled", "download_allowed", "collaborator_ids",
                  "allow_reshare", "allow_save"]

    def validate_content(self, value):
        from .rich_text import clean_content
        return clean_content(value)

    def validate_title(self, value):
        from .rich_text import clean_title
        return clean_title(value) or None

    def validate_topics(self, value):
        from .hashtags import clean_topics
        return clean_topics(value)

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
        # Reel studio: `metadata.reel` is written once, when the reel is created (reel_meta.clean_post_metadata).
        # An edit can never add / change it - the stored block is kept as it is (or stays absent).
        # X12: `metadata.class_recording` (who may open a saved class recording) is server-only: a client copy is dropped and
        # the stored one (if any) is kept, so an edit can neither add, widen nor remove the class lock (post/reel_meta.py).
        # X12: a class-recording post can be taken outside its class only by the classroom's teacher.
        if attrs.get("visibility") and self.instance is not None:
            from .audience import class_recording_widen_error
            widen = class_recording_widen_error(self.instance, self.context["request"].user, attrs["visibility"])
            if widen:
                raise serializers.ValidationError({"visibility": widen})
        if "metadata" in attrs and self.instance is not None:
            from .reel_meta import PROTECTED_KEYS, strip_protected_keys
            # a multipart edit can send `metadata` as a JSON string: decode it first so the key cannot hide in the string
            kept = strip_protected_keys(attrs["metadata"])
            if not isinstance(kept, dict):
                raise serializers.ValidationError({"metadata": "metadata must be a JSON object."})
            kept = dict(kept)
            kept.pop("reel", None)
            old_meta = self.instance.metadata if isinstance(self.instance.metadata, dict) else {}
            if isinstance(old_meta.get("reel"), dict):
                kept["reel"] = old_meta["reel"]
            for key in PROTECTED_KEYS:
                if key in old_meta:
                    kept[key] = old_meta[key]
            attrs["metadata"] = kept
        if "tags" in attrs:
            from .tagging import TagError, parse_tags
            visibility = attrs.get("visibility") or (self.instance.visibility if self.instance else "public")
            try:
                attrs["tags"] = parse_tags(attrs["tags"], self.context["request"].user, visibility)
            except TagError as exc:
                raise serializers.ValidationError({"tags": str(exc)})
        return attrs

    def update(self, instance, validated_data):
        tags = validated_data.pop("tags", None)
        collaborator_ids = validated_data.pop("collaborator_ids", None)
        if tags is not None:
            from .tagging import notify_post_tags_on_commit, sync_post_tags
            sync_post_tags(instance, tags)
            notify_post_tags_on_commit(instance)
        if "content" in validated_data and not validated_data.get("hashtags"):
            # same unicode-aware extraction as create (the old \w+ cut a Hindi #tag at its first matra)
            from .hashtags import extract_from_text
            validated_data["hashtags"] = extract_from_text(validated_data["content"] or "")
        if "hashtags" in validated_data:
            from .hashtags import normalize_list
            validated_data["hashtags"] = normalize_list(validated_data["hashtags"])

        # Round 5: keep the text as it was before this edit (title / caption / hashtags / audience) -> version history.
        from .post_options import HISTORY_FIELDS, snapshot_before_edit
        request = self.context["request"]
        changed = [f for f in HISTORY_FIELDS if f in validated_data and validated_data[f] != getattr(instance, f)]
        snapshot_before_edit(instance, request.user, changed)

        for field, value in validated_data.items():
            setattr(instance, field, value)
        if validated_data:
            # Topics / audience / comment + download settings are metadata: changing only them must not stamp the
            # post as "edited". Anything else (caption, title, hashtags, category ...) does, and sets `edited_at`.
            if set(validated_data.keys()) - self.META_ONLY:  # (tags were popped above)
                instance.is_edited = True
                instance.edited_at = timezone.now()
                instance.save(update_fields=list(validated_data.keys()) + ["is_edited", "edited_at", "updated_at"])
            else:
                instance.save(update_fields=list(validated_data.keys()) + ["updated_at"])
        if collaborator_ids:
            from . import collaborators as _collab
            try:
                _collab.invite(instance, request.user, collaborator_ids)
            except _collab.CollabError as exc:
                raise serializers.ValidationError({"collaborator_ids": exc.message})
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


MAX_STORY_HIDE_FROM = 100


class _IdListField(serializers.Field):
    """`hide_from` on POST /post/stories/create/ - user ids as a JSON-encoded list in the multipart body (or the
    list itself for a JSON body / upload queue replay). '' / 'null' mean none."""

    def to_internal_value(self, data):
        import json as _json
        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8", "ignore")
        if isinstance(data, str):
            data = data.strip()
            if data in ("", "null", "[]"):
                return []
            try:
                data = _json.loads(data)
            except ValueError:
                raise serializers.ValidationError("hide_from must be a list of user ids.")
        if not isinstance(data, list):
            raise serializers.ValidationError("hide_from must be a list of user ids.")
        ids = []
        for v in data:
            if isinstance(v, bool) or not isinstance(v, (int, str)):
                raise serializers.ValidationError("hide_from must be a list of user ids.")
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise serializers.ValidationError("hide_from must be a list of user ids.")
            if n < 1:
                raise serializers.ValidationError("hide_from must be a list of user ids.")
            if n not in ids:
                ids.append(n)
        if len(ids) > MAX_STORY_HIDE_FROM:
            raise serializers.ValidationError(f"You can hide a story from at most {MAX_STORY_HIDE_FROM} people.")
        return ids

    def to_representation(self, value):  # write-only in practice
        return value


class StoryCreateSerializer(serializers.ModelSerializer):
    # STORIES UPGRADE - PART 2: optional overlays (mention / link / poll / question).
    stickers = _StickersField(required=False, write_only=True)
    # STORIES UPGRADE - PART 3a: optional background track (CC0, from the music search).
    music = _MusicField(required=False, write_only=True, allow_null=True)
    # F3-AUD: people this story is hidden from (works with every audience).
    hide_from = _IdListField(required=False, write_only=True)

    class Meta:
        model = Story
        fields = [
            "id", "media", "media_type", "caption", "audience", "audience_classroom_id",
            "reply_setting", "allow_reshare", "allow_save", "expires_at", "created_at",
            "stickers", "music", "hide_from",
        ]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {
            "expires_at": {"required": False}, "audience": {"required": False},
            "audience_classroom_id": {"required": False, "allow_null": True},
            "reply_setting": {"required": False}, "allow_reshare": {"required": False},
            "allow_save": {"required": False},
        }

    def validate_media(self, file):
        # Reuses the same allowlist/size limit as post media — a story is
        # just a short-lived image/video, no reason to allow anything a
        # regular PostMedia upload wouldn't.
        return validate_media_file(file)

    def validate(self, attrs):
        owner = self.context["request"].user
        raw_stickers = attrs.pop("stickers", None) or []
        # S4b: what the app did not send (audience / allow reshare / allow save) starts from the author's account-level
        # defaults. Read from the RAW data: a multipart form that leaves a switch out would otherwise read as "off".
        from .content_defaults import story_fill
        attrs.update(story_fill(owner, getattr(self, "initial_data", None)))
        audience = attrs.get("audience") or Story.AUDIENCE_EVERYONE

        # F2: a client-supplied `expires_at` is clamped (never longer than STORY_MAX_LIFETIME_HOURS, never shorter than
        # STORY_MIN_LIFETIME_MINUTES) - otherwise a story could be asked to live forever.
        if attrs.get("expires_at") is not None:
            from .story_limits import clean_expires_at
            attrs["expires_at"] = clean_expires_at(attrs["expires_at"])

        # F3-AUD: a class story names ONE classroom the author belongs to; other audiences carry none.
        classroom_id = attrs.get("audience_classroom_id")
        if audience == Story.AUDIENCE_CLASS:
            from .audience import classroom_ids_of
            if not classroom_id:
                raise serializers.ValidationError({"audience_classroom_id": ["Choose a class for a class story."]})
            if classroom_id not in classroom_ids_of(owner):
                raise serializers.ValidationError({"audience_classroom_id": ["You are not a member of that class."]})
        else:
            attrs["audience_classroom_id"] = None
            classroom_id = None

        # F3-AUD: hide-from list = real, active, non-self users (unknown ids are dropped, not an error: the list
        # was built from the client's cache and a deleted account must not fail the upload).
        hide_ids = [i for i in (attrs.pop("hide_from", None) or []) if i != owner.id]
        if hide_ids:
            hide_ids = list(User.objects.filter(id__in=hide_ids, is_active=True).values_list("id", flat=True))
        attrs["_hide_from_ids"] = hide_ids

        try:
            attrs["_clean_stickers"] = clean_stickers(
                raw_stickers, owner, audience, classroom_id=classroom_id, hidden_ids=hide_ids,
            )
        except StickerError as exc:
            raise serializers.ValidationError({"stickers": [exc.as_text()]})
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        cleaned = validated_data.pop("_clean_stickers", [])
        hide_ids = validated_data.pop("_hide_from_ids", [])
        # Story + stickers are one unit: if a sticker row fails to write, the
        # story is not created half-decorated.
        with transaction.atomic():
            story = Story.objects.create(user=request.user, **validated_data)
            if cleaned:
                create_stickers(story, cleaned)
            if hide_ids:
                from .models import StoryHiddenFrom
                StoryHiddenFrom.objects.bulk_create(
                    [StoryHiddenFrom(story=story, user_id=uid) for uid in hide_ids], ignore_conflicts=True,
                )

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
        location -> {"name", "lat"?, "lng"?}          ({} when the author is a minor)
        countdown-> {"title", "ends_at"}              (ISO, UTC)
        quiz     -> {"question", "options", "my_answer": int|null, "correct_index": int|null,
                     "explanation": str|null, "results": {"counts", "total", "correct"} | null}
                    (`correct_index` / `explanation` / `results` only for the owner, or once the viewer answered)
        add_yours-> {"prompt", "chain_id", "count", "my_joined"}
        gif      -> {"id", "title", "url", "preview_url", "width", "height"}
        post_card-> {"post_id", "post_kind", "available", "title", "thumb", "author": {...}}  (built per viewer)

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
        if obj.kind == StorySticker.KIND_LOCATION:
            # F3 minor privacy: an old location sticker of an author who is a minor now is not shown (not rewritten).
            if minor_privacy.hide_location_for_author(obj.story.user):
                return {}
            return dict(obj.data or {})
        if obj.kind == StorySticker.KIND_POST_CARD:
            # F3-AUD: built per viewer at read time (story_post_card.py) - never trusts the stored copy.
            from . import story_post_card
            request = self.context.get("request")
            return story_post_card.build_preview(
                obj.data, getattr(request, "user", None), request, get_profile_pic_url,
            )
        if obj.kind in INTERACTIVE_KINDS:
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
        if any(s.kind in INTERACTIVE_KINDS for s in stickers):
            self.context["sticker_stats"] = build_interaction_stats(stickers, viewer)
        return super().to_representation(items)


class StorySerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    media = serializers.SerializerMethodField()
    is_viewed_by_me = serializers.SerializerMethodField()
    stickers = serializers.SerializerMethodField()
    music = serializers.SerializerMethodField()
    live = serializers.SerializerMethodField()
    can_reply = serializers.SerializerMethodField()
    can_reshare = serializers.SerializerMethodField()  # X1
    can_save = serializers.SerializerMethodField()  # X1
    hide_from_count = serializers.SerializerMethodField()

    class Meta:
        model = Story
        list_serializer_class = StoryListSerializer
        fields = [
            "id", "user", "media", "media_type", "caption", "audience", "audience_classroom_id",
            "reply_setting", "allow_reshare", "allow_save", "can_reply", "can_reshare", "can_save", "hide_from_count",
            "views_count", "is_viewed_by_me", "created_at", "expires_at",
            "stickers", "music", "live",
        ]

    def get_can_reply(self, obj):
        # F3-AUD: the viewer's own answer to "may I reply / react?" so the client can hide the reply bar.
        request = self.context.get("request")
        viewer = getattr(request, "user", None)
        if viewer is None or not viewer.is_authenticated:
            return False
        from .story_visibility import can_reply_to_story
        return can_reply_to_story(obj, viewer)

    def get_can_reshare(self, obj):
        # X1: the viewer's own answer to "may I send this story in a chat?" (owner: always; else allow_reshare).
        from .story_visibility import can_reshare_story
        request = self.context.get("request")
        return can_reshare_story(obj, getattr(request, "user", None))

    def get_can_save(self, obj):
        # X1: same for "may I download it?" (allow_save).
        from .story_visibility import can_save_story
        request = self.context.get("request")
        return can_save_story(obj, getattr(request, "user", None))

    def get_hide_from_count(self, obj):
        # Owner only (a viewer must never learn the list exists).
        request = self.context.get("request")
        viewer = getattr(request, "user", None)
        if viewer is None or not viewer.is_authenticated or viewer.id != obj.user_id:
            return None
        return obj.hidden_from.count()

    def get_live(self, obj):
        # LV1: {session_id, title, started_at, viewer_count, classroom_id, can_join}
        # when this story's author is live right now AND the viewer may see it,
        # else null. The map is computed ONCE per request by the list view
        # (post/story_live.py); any other caller that doesn't pass `live_map`
        # in the context simply gets null (no extra queries).
        live_map = self.context.get("live_map")
        if not live_map:
            return None
        return live_map.get(obj.user_id)

    def get_music(self, obj):
        # Stored already-cleaned (post/story_music.py); NULL = no music.
        music = obj.music or None
        if music:
            # X7: a sound staff took down stops playing on stories that already carry it.
            from .sounds import is_blocked

            if is_blocked(music.get("id")):
                return None
        return music

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
    # User ids are integers (AutoField) — this was a UUIDField, which made
    # every mute request fail validation.
    user_id = serializers.IntegerField(min_value=1)
    # P8: what to mute. Both omitted = "everything" (the old contract, so older app builds keep working).
    posts = serializers.BooleanField(required=False)
    stories = serializers.BooleanField(required=False)

    def validate(self, attrs):
        return _validate_mute_scope(attrs)


class MuteScopeSerializer(serializers.Serializer):
    """PATCH /post/muted-accounts/<user_id>/ body - at least one of posts / stories."""
    posts = serializers.BooleanField(required=False)
    stories = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if 'posts' not in attrs and 'stories' not in attrs:
            raise serializers.ValidationError('Send posts and/or stories.')
        return attrs


def _validate_mute_scope(attrs):
    """Explicit `posts: false` AND `stories: false` is not a mute at all (use unmute)."""
    if attrs.get('posts') is False and attrs.get('stories') is False:
        raise serializers.ValidationError('Choose posts, stories or both - or unmute instead.')
    return attrs


class MutedAccountSerializer(serializers.ModelSerializer):
    user = _FeedbackUserSerializer(source='muted_user', read_only=True)

    class Meta:
        model = _MutedAccount
        fields = ['id', 'user', 'mute_posts', 'mute_stories', 'created_at']
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


class WhyExperimentSerializer(serializers.Serializer):
    name = serializers.CharField()
    bucket = serializers.IntegerField()
    variant = serializers.CharField()


class WhyResponseSerializer(serializers.Serializer):
    experiment = WhyExperimentSerializer()
    stages = serializers.ListField(child=serializers.DictField())
    post_id = serializers.UUIDField()
    feed_source = serializers.CharField(allow_null=True)
    reasons = WhyReasonSerializer(many=True)
    dampened = WhyDampenedSerializer(many=True)
    config = serializers.DictField(required=False)  # T1 item 7: staff only - the exact knobs in force
