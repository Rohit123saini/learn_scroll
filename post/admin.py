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
    PostComment,
    PostLike,
    PostMedia,
    PostSave,
    PostShare,
    PostView,
    Story,
    StoryView,
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
    )
    autocomplete_fields = ("user",)
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

@admin.register(Story)
class StoryAdmin(admin.ModelAdmin):
    # NEW — checklist items 54/55/57/60.
    list_display = ("id", "user", "media_type", "views_count", "is_deleted", "created_at", "expires_at")
    list_filter = ("media_type", "is_deleted")
    search_fields = ("user__username",)
    readonly_fields = ("views_count",)


@admin.register(StoryView)
class StoryViewAdmin(admin.ModelAdmin):
    list_display = ("id", "story", "user", "viewed_at")
    search_fields = ("story__id", "user__username")