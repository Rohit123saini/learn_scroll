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
    PostAnswerAttachment,
    PostAnswerStep,
    PostComment,
    PostDoubt,
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
    StoryQuizAnswer,
    CloseFriend,
    Highlight,
    HighlightItem,
    UserInterest,
    MutedAccount,
    PostHide,
    FeedFeedback,
    PostPoll,
    PostPollOption,
    PostPollVote,
    PostPollQuestionLink,
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
    inlines = []  # filled below (steps / attachments)
    list_display = ("id", "post", "user", "is_best_answer", "likes_count", "created_at")
    list_filter = ("is_best_answer",)
    search_fields = ("content", "user__username", "post__id")
    autocomplete_fields = ("user",)
    raw_id_fields = ("post",)


class PostAnswerStepInline(admin.TabularInline):
    model = PostAnswerStep
    extra = 0


class PostAnswerAttachmentInline(admin.TabularInline):
    model = PostAnswerAttachment
    extra = 0


PostAnswerAdmin.inlines = [PostAnswerStepInline, PostAnswerAttachmentInline]


# Doubt post upgrade: subject / urgent / solved / bounty of a doubt. Bounty fields are read-only: coins only move
# through post/doubts.py (CoinLedger), never by editing a row here.
@admin.register(PostDoubt)
class PostDoubtAdmin(admin.ModelAdmin):
    list_display = ("post", "subject", "class_level", "is_urgent", "bounty_coins", "bounty_status", "solved_at", "created_at")
    list_filter = ("is_urgent", "bounty_status", "subject")
    search_fields = ("subject", "chapter", "ocr_text", "post__id")
    raw_id_fields = ("post", "bounty_paid_to")
    readonly_fields = ("bounty_coins", "bounty_status", "bounty_paid_to", "solved_at", "teachers_notified_at")


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


@admin.register(StoryQuizAnswer)
class StoryQuizAnswerAdmin(admin.ModelAdmin):
    list_display = ("id", "sticker", "user", "option_index", "is_correct", "created_at")
    search_fields = ("sticker__id", "user__username")
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


# ---------------------------------------------------------------------------
# P1 — resource files + hashtags
# ---------------------------------------------------------------------------
from .models import Hashtag, PostAttachment, PostAttachmentVersion, ResourcePaper  # noqa: E402


@admin.register(PostAttachment)
class PostAttachmentAdmin(admin.ModelAdmin):
    list_display = ("original_name", "kind", "mime", "size", "pages", "version", "text_source", "post", "created_at")
    list_filter = ("kind",)
    search_fields = ("original_name", "post__title")
    raw_id_fields = ("post",)


@admin.register(Hashtag)
class HashtagAdmin(admin.ModelAdmin):
    list_display = ("name", "posts_count", "last_used_at")
    search_fields = ("name",)
    ordering = ("-posts_count",)


@admin.register(PostAttachmentVersion)
class PostAttachmentVersionAdmin(admin.ModelAdmin):
    list_display = ("attachment", "version", "original_name", "size", "archived_at")
    search_fields = ("original_name", "attachment__post__title")
    raw_id_fields = ("attachment", "replaced_by")


@admin.register(ResourcePaper)
class ResourcePaperAdmin(admin.ModelAdmin):
    list_display = ("post", "year", "doi", "venue")
    list_filter = ("year",)
    search_fields = ("authors_text", "doi", "venue", "post__title")
    raw_id_fields = ("post",)


# ---------------------------------------------------------------------------
# Poll + Quiz. Vote rows of an ANONYMOUS poll exist only as the double-vote guard; the admin list hides the voter
# for them too, so staff browsing votes never de-anonymises anyone. Votes are read-only (changing one would
# desync the denormalised counters).
# ---------------------------------------------------------------------------
class PostPollOptionInline(admin.TabularInline):
    model = PostPollOption
    extra = 0
    fields = ("display_order", "text", "image", "is_correct", "votes_count")
    readonly_fields = ("votes_count",)


@admin.register(PostPoll)
class PostPollAdmin(admin.ModelAdmin):
    list_display = (
        "id", "post", "is_quiz", "is_anonymous", "allow_multiple", "results_visibility",
        "total_votes_count", "expires_at", "created_at",
    )
    list_filter = ("is_quiz", "is_anonymous", "allow_multiple", "results_visibility")
    search_fields = ("post__id", "post__title", "post__content", "post__user__username")
    raw_id_fields = ("post",)
    readonly_fields = ("total_votes_count", "created_at")
    inlines = [PostPollOptionInline]
    date_hierarchy = "created_at"


@admin.register(PostPollVote)
class PostPollVoteAdmin(admin.ModelAdmin):
    list_display = ("id", "poll", "option", "voter", "is_correct", "created_at")
    list_filter = ("is_correct", "poll__is_quiz", "poll__is_anonymous")
    search_fields = ("poll__id", "poll__post__id")
    raw_id_fields = ("poll", "option", "user")
    list_select_related = ("poll", "option", "user")

    def get_fields(self, request, obj=None):
        if obj is not None and obj.poll.is_anonymous:
            return ("poll", "option", "is_correct", "created_at")
        return ("poll", "option", "user", "is_correct", "created_at")

    def get_readonly_fields(self, request, obj=None):
        return self.get_fields(request, obj)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="voter")
    def voter(self, obj):
        return "(anonymous)" if obj.poll.is_anonymous else obj.user.username


@admin.register(PostPollQuestionLink)
class PostPollQuestionLinkAdmin(admin.ModelAdmin):
    list_display = ("id", "poll", "series_id", "question_id", "created_by", "created_at")
    search_fields = ("series_id", "question_id", "created_by__username")
    raw_id_fields = ("poll", "created_by")


# ----------------------------------------------------------------------------------------------------------------- X7
from django.contrib import messages  # noqa: E402

from . import sounds as _sounds  # noqa: E402
from .models import SoundReport, SoundTakedown  # noqa: E402


@admin.register(SoundTakedown)
class SoundTakedownAdmin(admin.ModelAdmin):
    """Blocked reel sounds. Rows are made by the "Block" actions on SoundReport (they tell the reel owners and can send the
    reels to review) or by the staff API; a row is never added by hand here, because then nobody would be told."""
    list_display = ("sound_id", "title", "artist", "status", "reason", "owners_told", "reels_held", "created_by", "created_at")
    list_filter = ("status", "reason")
    search_fields = ("sound_id", "title", "artist", "note")
    readonly_fields = ("owners_told", "reels_held", "created_by", "created_at", "restored_by", "restored_at")
    actions = ["restore_selected"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Restore selected sounds (they can be used again)")
    def restore_selected(self, request, queryset):
        n = 0
        for row in queryset.filter(status=SoundTakedown.Status.BLOCKED):
            n += _sounds.restore_sound(row.sound_id, by=request.user)
        self.message_user(request, f"{n} sound(s) restored. Reels that were sent to review stay in the review queue.")


@admin.register(SoundReport)
class SoundReportAdmin(admin.ModelAdmin):
    list_display = ("sound_id", "reason", "status", "user", "updated_at")
    list_filter = ("status", "reason")
    search_fields = ("sound_id", "note", "user__username")
    raw_id_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")
    actions = ["block_selected", "block_and_review_selected", "dismiss_selected"]

    def _block(self, request, queryset, hold):
        done = 0
        for sid in sorted(set(queryset.values_list("sound_id", flat=True))):
            try:
                _sounds.block_sound(sid, by=request.user, reason="copyright", hold_reels=hold)
                done += 1
            except _sounds.SoundError as exc:
                self.message_user(request, f"{sid}: {exc}", level=messages.WARNING)
        self.message_user(request, f"{done} sound(s) blocked; the owners of reels that use them were told.")

    @admin.action(description="Block these sounds (tell reel owners)")
    def block_selected(self, request, queryset):
        self._block(request, queryset, False)

    @admin.action(description="Block these sounds AND send their reels to the review queue")
    def block_and_review_selected(self, request, queryset):
        self._block(request, queryset, True)

    @admin.action(description="Dismiss (false alarm)")
    def dismiss_selected(self, request, queryset):
        n = 0
        for sid in sorted(set(queryset.values_list("sound_id", flat=True))):
            n += _sounds.dismiss_reports(sid)
        self.message_user(request, f"{n} report(s) dismissed.")
