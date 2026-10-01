"""
post/admin.py

⚠️ CRITICAL FIX — same problem as serializers.py: the uploaded file did
`from .models import Comment, Hashtag, Like, Post, PostMedia, SavedPost,
Story, StoryView` — only `Post` and `PostMedia` of those actually exist.
This would raise `ImportError` on Django startup (admin.py is imported
eagerly by Django's app registry), taking down the whole project, not
just this app. Rewritten to register what's actually in models.py, with
real list_display/search_fields/filters instead of the doc's bare
`admin.site.register(...)` calls (post_app.md §9) — genuinely usable in
production, not just "doesn't crash".
"""
from django.contrib import admin

from .models import (
    ChunkedUpload,
    CommentLike,
    CommentMedia,
    Post,
    PostAnswer,
    PostComment,
    PostLike,
    PostMedia,
    PostSave,
    PostShare,
    PostView,
    Story,
    StoryView,
    StoryReaction,
    StorySticker,
    StoryPollVote,
    StoryQuestionAnswer,
    CloseFriend,
    Highlight,
    HighlightItem,
    UserInterest,
    MutedAccount,
    PostHide,
    FeedFeedback,
)


class PostMediaInline(admin.TabularInline):
    model = PostMedia
    extra = 0
    readonly_fields = ("file_name", "file_size_bytes", "mime_type", "width", "height", "duration_seconds")


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "category", "post_type", "visibility", "moderation_status", "likes_count", "comments_count", "is_deleted", "created_at")
    list_filter = ("category", "post_type", "visibility", "moderation_status", "is_deleted", "is_sensitive")
    search_fields = ("title", "content", "user__username", "slug")
    inlines = [PostMediaInline]
    readonly_fields = (
        "likes_count", "comments_count", "shares_count", "views_count", "saves_count",
        "like_count", "confuse_count", "wrong_count", "imp_count", "explain_count",
        "reposts_count",
    )
    autocomplete_fields = ("user",)
    # A plain <select> of every Post row would be unusable here.
    raw_id_fields = ("original_post",)
    date_hierarchy = "created_at"


@admin.register(PostMedia)
class PostMediaAdmin(admin.ModelAdmin):
    list_display = ("id", "post", "media_type", "file_name", "display_order", "created_at")
    list_filter = ("media_type",)
    search_fields = ("file_name", "post__id")


@admin.register(PostLike)
class PostLikeAdmin(admin.ModelAdmin):
    list_display = ("id", "post", "user", "reaction_type", "created_at")
    list_filter = ("reaction_type",)
    search_fields = ("user__username", "post__id")
    autocomplete_fields = ("user",)


# TASK G6 — "Ask a doubt" answers.
@admin.register(PostAnswer)
class PostAnswerAdmin(admin.ModelAdmin):
    list_display = ("id", "post", "user", "is_best_answer", "likes_count", "created_at")
    list_filter = ("is_best_answer",)
    search_fields = ("content", "user__username", "post__id")
    autocomplete_fields = ("user",)
    raw_id_fields = ("post",)


@admin.register(PostComment)
class PostCommentAdmin(admin.ModelAdmin):
    list_display = ("id", "post", "user", "parent", "is_hidden", "is_deleted", "created_at")
    list_filter = ("is_hidden", "is_deleted", "is_pinned")
    search_fields = ("content", "user__username", "post__id")
    autocomplete_fields = ("user", "post", "parent")


@admin.register(CommentMedia)
class CommentMediaAdmin(admin.ModelAdmin):
    list_display = ("id", "comment", "media_type", "file_name", "created_at")
    list_filter = ("media_type",)
    search_fields = ("file_name",)


@admin.register(PostShare)
class PostShareAdmin(admin.ModelAdmin):
    list_display = ("id", "post", "user", "created_at")
    search_fields = ("user__username", "post__id")


@admin.register(PostView)
class PostViewAdmin(admin.ModelAdmin):
    list_display = ("id", "post", "user", "ip_address", "viewed_at")
    search_fields = ("post__id", "user__username")


@admin.register(PostSave)
class PostSaveAdmin(admin.ModelAdmin):
    list_display = ("id", "post", "user", "collection_name", "created_at")
    list_filter = ("collection_name",)
    search_fields = ("post__id", "user__username")


@admin.register(ChunkedUpload)
class ChunkedUploadAdmin(admin.ModelAdmin):
    # 🔥 FIX: post_app.md §9 explicitly notes ChunkedUpload and CommentLike
    # weren't registered at all, so stuck/orphaned chunked uploads had no
    # way to be inspected or cleaned up from admin.
    list_display = ("upload_id", "user", "file_name", "total_chunks", "total_size", "is_completed", "created_at")
    list_filter = ("is_completed",)
    search_fields = ("upload_id", "file_name", "user__username")


@admin.register(CommentLike)
class CommentLikeAdmin(admin.ModelAdmin):
    list_display = ("id", "comment", "user", "reaction_type", "created_at")
    list_filter = ("reaction_type",)
    search_fields = ("user__username", "comment__id")

class StoryStickerInline(admin.TabularInline):
    # STORIES UPGRADE - PART 2: overlays are edited/inspected on the story.
    model = StorySticker
    extra = 0
    fields = ("kind", "mentioned_user", "x", "y", "rotation", "scale", "z_index", "data")
    raw_id_fields = ("mentioned_user",)


@admin.register(Story)
class StoryAdmin(admin.ModelAdmin):
    # NEW — checklist items 54/55/57/60.
    list_display = ("id", "user", "media_type", "audience", "views_count", "is_deleted", "created_at", "expires_at")
    list_filter = ("media_type", "audience", "is_deleted")
    search_fields = ("user__username",)
    readonly_fields = ("views_count",)
    inlines = [StoryStickerInline]


@admin.register(StorySticker)
class StoryStickerAdmin(admin.ModelAdmin):
    list_display = ("id", "story", "kind", "mentioned_user", "created_at")
    list_filter = ("kind",)
    search_fields = ("story__id", "mentioned_user__username")
    raw_id_fields = ("story", "mentioned_user")


@admin.register(StoryPollVote)
class StoryPollVoteAdmin(admin.ModelAdmin):
    list_display = ("id", "sticker", "user", "option_index", "created_at")
    search_fields = ("sticker__id", "user__username")
    raw_id_fields = ("sticker", "user")


@admin.register(StoryQuestionAnswer)
class StoryQuestionAnswerAdmin(admin.ModelAdmin):
    list_display = ("id", "sticker", "user", "created_at")
    search_fields = ("sticker__id", "user__username", "text")
    raw_id_fields = ("sticker", "user")


@admin.register(StoryView)
class StoryViewAdmin(admin.ModelAdmin):
    list_display = ("id", "story", "user", "viewed_at")
    search_fields = ("story__id", "user__username")


@admin.register(StoryReaction)
class StoryReactionAdmin(admin.ModelAdmin):
    list_display = ("id", "story", "user", "emoji", "created_at")
    search_fields = ("story__id", "user__username", "emoji")


@admin.register(CloseFriend)
class CloseFriendAdmin(admin.ModelAdmin):
    # STORIES UPGRADE - PART 1.
    list_display = ("id", "owner", "friend", "created_at")
    search_fields = ("owner__username", "friend__username")
    raw_id_fields = ("owner", "friend")


class HighlightItemInline(admin.TabularInline):
    # STORIES UPGRADE - PART 3b.
    model = HighlightItem
    extra = 0
    fields = ("story", "position", "created_at")
    readonly_fields = ("created_at",)
    raw_id_fields = ("story",)


@admin.register(Highlight)
class HighlightAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "title", "created_at", "updated_at")
    search_fields = ("user__username", "title")
    raw_id_fields = ("user", "cover_item")
    inlines = [HighlightItemInline]


@admin.register(HighlightItem)
class HighlightItemAdmin(admin.ModelAdmin):
    list_display = ("id", "highlight", "story", "position", "created_at")
    raw_id_fields = ("highlight", "story")


@admin.register(UserInterest)
class UserInterestAdmin(admin.ModelAdmin):
    # TASK 3 — feed personalization (production_readiness_tasks.md).
    list_display = ("id", "user", "category", "created_at")
    list_filter = ("category",)
    search_fields = ("user__username",)


@admin.register(PostHide)
class PostHideAdmin(admin.ModelAdmin):
    # Feed feedback controls (Part 1): "Not interested".
    list_display = ("id", "user", "post", "reason", "created_at")
    list_filter = ("reason",)
    search_fields = ("user__username", "post__id")
    raw_id_fields = ("user", "post")


@admin.register(MutedAccount)
class MutedAccountAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "muted_user", "created_at")
    search_fields = ("user__username", "muted_user__username")
    raw_id_fields = ("user", "muted_user")


@admin.register(FeedFeedback)
class FeedFeedbackAdmin(admin.ModelAdmin):
    # Feed feedback controls (Part 2): "Show fewer like this" negative ranking signal.
    # `weight` is the value AT updated_at; ranking decays it (feed_mix.decay_weight).
    list_display = ("id", "user", "kind", "key", "weight", "updated_at")
    list_filter = ("kind",)
    search_fields = ("user__username", "key")
    raw_id_fields = ("user",)
