# post/models.py--
import uuid
from datetime import timedelta
from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import FileExtensionValidator
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.conf import settings
from django.utils import timezone
User = get_user_model()


class Post(models.Model):
    CATEGORY_CHOICES = [
        ('general', 'General'),
        ('tech', 'Technology'),
        ('jobs', 'Jobs'),
        ('news', 'News'),
        ('education', 'Education'),
        ('business', 'Business'),
        ('entertainment', 'Entertainment'),
        ('sports', 'Sports'),
        ('lifestyle', 'Lifestyle'),
        ('other', 'Other'),
    ]

    POST_TYPE_CHOICES = [
        ('text', 'Text'),
        ('image', 'Image'),
        ('video', 'Video'),
        ('document', 'Document'),
        ('poll', 'Poll'),
        ('article', 'Article'),
        ('carousel', 'Carousel'),
        ('link', 'Link'),
    ]

    VISIBILITY_CHOICES = [
        ('public', 'Public'),
        ('connections', 'Connections'),
        ('private', 'Private'),
    ]

    MODERATION_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('flagged', 'Flagged'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='posts')

    # Content
    content = models.TextField(blank=True, null=True)
    title = models.CharField(max_length=300, blank=True, null=True)
    category = models.CharField(max_length=100, choices=CATEGORY_CHOICES, default='general',
                                db_index=True)  # 🔥 Category field

    # Post Type
    post_type = models.CharField(max_length=20, choices=POST_TYPE_CHOICES, default='text', db_index=True)
    visibility = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default='public')

    # Engagement Counters - Denormalized for performance
    likes_count = models.PositiveIntegerField(default=0)
    comments_count = models.PositiveIntegerField(default=0)
    shares_count = models.PositiveIntegerField(default=0)
    views_count = models.BigIntegerField(default=0)
    saves_count = models.PositiveIntegerField(default=0)

    like_count = models.PositiveIntegerField(default=0)
    confuse_count = models.PositiveIntegerField(default=0)
    wrong_count = models.PositiveIntegerField(default=0)
    imp_count = models.PositiveIntegerField(default=0)
    explain_count = models.PositiveIntegerField(default=0)
    # Flags
    is_edited = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False, db_index=True)
    is_pinned = models.BooleanField(default=False)
    is_comments_disabled = models.BooleanField(default=False)
    is_sensitive = models.BooleanField(default=False)

    # Moderation
    moderation_status = models.CharField(max_length=20, choices=MODERATION_CHOICES, default='approved')
    reported_count = models.PositiveIntegerField(default=0)

    # SEO & Search
    slug = models.SlugField(max_length=500, unique=True, blank=True, null=True)
    hashtags = models.JSONField(default=list, blank=True)  # ['flutter', 'tech']
    mentioned_user_ids = models.JSONField(default=list, blank=True)  # [uuid1, uuid2]

    # Metadata - Poll options, article data, etc
    metadata = models.JSONField(default=dict, blank=True)
    location = models.JSONField(default=dict, blank=True)  # {"city": "Meerut", "country": "India"}

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(blank=True, null=True)
    published_at = models.DateTimeField(blank=True, null=True)  # For scheduled posts

    class Meta:
        db_table = 'posts'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at', 'is_deleted']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['category', '-created_at']),
            models.Index(fields=['-likes_count', '-created_at']),  # For trending
        ]

    def __str__(self):
        return f"{self.user.username} - {self.category} - {self.created_at}"


class PostMedia(models.Model):
    MEDIA_TYPE_CHOICES = [
        ('image', 'Image'),
        ('video', 'Video'),
        ('document', 'Document'),
        ('audio', 'Audio'),
        ('gif', 'GIF'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='media')

    # File Info - 🔥 Har type ki file
    media_type = models.CharField(max_length=20, choices=MEDIA_TYPE_CHOICES)
    file = models.FileField(
        upload_to='posts/%Y/%m/%d/',
        validators=[FileExtensionValidator(
            allowed_extensions=['jpg', 'jpeg', 'png', 'gif', 'mp4', 'mov', 'avi',
                                'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
                                'mp3', 'wav', 'zip', 'txt']
        )]
    )
    thumbnail = models.ImageField(upload_to='posts/thumbnails/%Y/%m/%d/', blank=True, null=True)
    file_name = models.CharField(max_length=500)
    file_size_bytes = models.BigIntegerField()
    mime_type = models.CharField(max_length=100)

    # Media Specific
    width = models.PositiveIntegerField(blank=True, null=True)
    height = models.PositiveIntegerField(blank=True, null=True)
    duration_seconds = models.PositiveIntegerField(blank=True, null=True)  # For video/audio
    page_count = models.PositiveIntegerField(blank=True, null=True)  # For PDFs

    # CDN & Storage
    cdn_url = models.URLField(blank=True, null=True)
    blur_hash = models.CharField(max_length=100, blank=True, null=True)  # For image placeholder

    # Order in carousel
    display_order = models.PositiveIntegerField(default=0)

    # Metadata
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_media'
        ordering = ['display_order', 'created_at']
        indexes = [
            models.Index(fields=['post', 'display_order']),
        ]

    def __str__(self):
        return f"{self.media_type} - {self.file_name}"


class PostLike(models.Model):
    REACTION_CHOICES = [
        ('like', 'like'),  # 👍
        ('confuse', 'confuse'),  # 🤔
        ('wrong', 'wrong'),  # ❗
        ('imp', 'imp'),  # ⭐
        ('explain', 'explain'),  # 💡
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='likes')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_likes')
    reaction_type = models.CharField(max_length=20, choices=REACTION_CHOICES, default='like')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_likes'
        unique_together = ['post', 'user']  # Ek user ek hi baar
        indexes = [
            models.Index(fields=['post', '-created_at']),
            models.Index(fields=['user', '-created_at']),
        ]



class PostComment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='comments')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='post_comments')
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='replies')

    content = models.TextField(blank=True)
    likes_count = models.PositiveIntegerField(default=0)
    replies_count = models.PositiveIntegerField(default=0)

    is_edited = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False, db_index=True)
    is_pinned = models.BooleanField(default=False)
    # 🔥 NEW FIELD FOR HIDE
    is_hidden = models.BooleanField(default=False, db_index=True)
    hidden_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='hidden_comments')
    hidden_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'post_comments'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['post', '-created_at', 'is_hidden', 'is_deleted']),
            models.Index(fields=['parent', '-created_at']),
        ]

class CommentMedia(models.Model):
    MEDIA_TYPES = (
        ('image', 'Image'),
        ('video', 'Video'),
        ('audio', 'Audio'),  # 🔥 voice, mp3
        ('document', 'Document'),
        ('other', 'Other'), # 🔥 koi bhi file
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    comment = models.ForeignKey(PostComment, on_delete=models.CASCADE, related_name='media')
    media_type = models.CharField(max_length=20, choices=MEDIA_TYPES)
    file = models.FileField(upload_to='comment_media/%Y/%m/%d/') # 🔥 No validator = sab kuch acceptable
    file_name = models.CharField(max_length=500, blank=True)
    file_size = models.BigIntegerField(default=0)
    mime_type = models.CharField(max_length=150, blank=True) # 🔥 add kar diya

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'comment_media'

    def save(self, *args, **kwargs):
        if self.file and not self.file_name:
            self.file_name = self.file.name
        super().save(*args, **kwargs)

class PostShare(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='shares')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    share_text = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_shares'
        unique_together = ['post', 'user']


class PostView(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='views')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True, null=True)
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_views'
        indexes = [
            models.Index(fields=['post', '-viewed_at']),
        ]


class PostSave(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='saved_by')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='saved_posts')
    collection_name = models.CharField(max_length=100, default='default')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_saves'
        unique_together = ['post', 'user']
        indexes = [
            models.Index(fields=['user', '-created_at']),
        ]


# NOTE (fix, see post_app.md §14 issues #3 & #4):
#
# `update_likes_count` REMOVED — it duplicated `update_reaction_counts`
# (defined further below), which already recomputes `likes_count` as the
# sum of all per-reaction-type counts. Having both fire on every
# PostLike save/delete meant two separate COUNT queries + two separate
# UPDATE statements per like/unlike, always converging on the same
# number — pure redundancy, no correctness bug, just wasted DB round
# trips. `update_reaction_counts` was the superset (it also set
# like_count/confuse_count/wrong_count/imp_count/explain_count and the
# auto-flag-on-5-wrong logic) — see B-5 further down: that receiver has
# since been removed from this file too, in favor of the equivalent
# (and now sole) `post.signals.sync_post_reaction_counts`.
#
# `update_comments_count` REMOVED ENTIRELY — this one WAS a real
# correctness bug, not just redundant work. It recomputed
# `Post.comments_count` to the true absolute count on every
# PostComment save (create AND soft-delete). But `views.py`'s
# CommentCreateAPIView and `comment_view.py`'s CommentDeleteAPIView
# *also* separately do `F('comments_count') + 1` / `- 1` right after
# calling `.save()`/`.create()` — which fires this signal first. Net
# effect: every top-level comment create double-incremented
# comments_count by 1 extra, and every top-level delete
# double-decremented it by 1 extra. The count would silently drift
# further from reality with every create/delete.
#
# Removing the signal fixes both directions at once, because the
# manual F()-based updates already scattered through the views
# (create/+1, delete/-1) are correct on their own — same pattern
# already used for `replies_count`, which never had a signal and never
# had this bug. CommentHideAPIView is unaffected: it never relied on
# this signal (the signal doesn't know about `is_hidden`), it already
# does its own manual +1/-1 — see post_app.md §14 issue #3's note,
# which is still accurate for the hide/unhide path specifically.
@receiver(post_save, sender=PostShare)
@receiver(post_delete, sender=PostShare)
def update_shares_count(sender, instance, **kwargs):
    Post.objects.filter(id=instance.post_id).update(
        shares_count=PostShare.objects.filter(post_id=instance.post_id).count()
    )

@receiver(post_save, sender=PostSave)
@receiver(post_delete, sender=PostSave)
def update_saves_count(sender, instance, **kwargs):
    Post.objects.filter(id=instance.post_id).update(
        saves_count=PostSave.objects.filter(post_id=instance.post_id).count()
    )


# ---------------------------------------------------------------------------
# TASK 27 — video thumbnail generation moved OUT of models.py.
#
# What used to live here (`auto_generate_video_thumbnail`, a `post_save`
# receiver on `PostMedia`) had two real problems, on top of not belonging
# in models.py in the first place (business logic mixed into the model
# module, an `ffmpeg-python` import pulled in just for this one signal):
#
#   1. It ran ffmpeg SYNCHRONOUSLY inside the `post_save` signal — i.e.
#      inline in whatever request created the `PostMedia` row
#      (`PostCreateAPIView.post()`). Every video upload's response time
#      included however long ffmpeg took to extract a frame.
#   2. On S3/GCS (`USE_S3_STORAGE=true`, task 26) it detected
#      `instance.file.path` raising `NotImplementedError`, logged a
#      warning, and just... gave up. Cloud-storage uploads never got a
#      thumbnail at all — not a crash, but not a fix either.
#
# Replaced by (see post/signals.py, post/tasks.py, post/services.py):
#   - `post.signals.queue_video_thumbnail_on_create` — the ONLY thing
#     still triggered by `PostMedia`'s `post_save`; it does nothing but
#     `generate_video_thumbnail.delay(instance.id)` and return.
#   - `post.tasks.generate_video_thumbnail` — the actual Celery task.
#     Runs off the request path, so ffmpeg's runtime no longer affects
#     upload latency.
#   - `post.services.download_storage_file_to_temp` /
#     `.generate_video_thumbnail_file` — read the source file via the
#     storage API (`.open()` + chunked read) instead of `.path`, which
#     works identically for local disk AND S3/GCS. This is what actually
#     fixes case 2 above instead of just logging around it: cloud-stored
#     videos now get real thumbnails too, not a permanent skip.
#   - Uses the `ffmpeg` CLI via `subprocess` (already a hard runtime
#     dependency either way — a server without the `ffmpeg` binary
#     installed couldn't run the old `ffmpeg-python` wrapper either)
#     instead of the `ffmpeg-python` package, so no extra pip dependency
#     was added for this fix.
# ---------------------------------------------------------------------------

# NOTE (fix, see B-5): `update_reaction_counts` REMOVED FROM HERE.
#
# It was a second `post_save`/`post_delete` receiver on `PostLike`,
# running alongside `post.signals.sync_post_reaction_counts` — both
# converged on the same numbers (not a correctness bug), but every
# like/unlike paid for two full aggregate-recompute + UPDATE round
# trips on the app's hottest write path. `sync_post_reaction_counts`
# is the superset (same per-type + total counts) and is the one kept;
# its 5+-wrong auto-flag logic now lives there too, folded into the
# same aggregate query and the same UPDATE instead of a second one —
# see post/signals.py.


import uuid
from django.conf import settings

class ChunkedUpload(models.Model):
    upload_id = models.CharField(max_length=100, unique=True)
    file_name = models.CharField(max_length=500)
    total_chunks = models.IntegerField()
    total_size = models.BigIntegerField()
    post_id = models.CharField(max_length=100)
    parent_id = models.CharField(max_length=100, null=True, blank=True)
    content = models.TextField(blank=True)
    # FIX: Yaha kabhi bhi 'authapp.User' mat likho, ye use karo
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    is_completed = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.upload_id} - {self.file_name}"



class CommentLike(models.Model):
    REACTION_CHOICES = [
        ('like', 'like'),
        ('confuse', 'confuse'),
        ('wrong', 'wrong'),
        ('imp', 'imp'),
        ('explain', 'explain'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    comment = models.ForeignKey(PostComment, on_delete=models.CASCADE, related_name='likes')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='comment_likes')
    reaction_type = models.CharField(max_length=20, choices=REACTION_CHOICES, default='like')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'comment_likes'
        unique_together = ['comment', 'user']
        indexes = [
            models.Index(fields=['comment', '-created_at']),
        ]

@receiver(post_save, sender=CommentLike)
@receiver(post_delete, sender=CommentLike)
def update_comment_reaction_counts(sender, instance, **kwargs):
    from django.db.models import Count
    comment_id = instance.comment_id
    total = CommentLike.objects.filter(comment_id=comment_id).count()
    PostComment.objects.filter(id=comment_id).update(likes_count=total)

# ---------------------------------------------------------------------------
# Story / StoryView (checklist items 54/55/57/60).
#
# Added here for real — the previously-uploaded Tasks.py assumed this
# model already existed and it didn't. Follows the exact same pattern as
# the rest of this app: UUID pk, soft-delete (is_deleted/deleted_at),
# `-created_at` ordering, denormalized counter kept in sync by a signal
# (same shape as update_saves_count/update_shares_count above).
# ---------------------------------------------------------------------------
def default_story_expiry():
    return timezone.now() + timedelta(hours=24)


class Story(models.Model):
    MEDIA_TYPE_CHOICES = [
        ('image', 'Image'),
        ('video', 'Video'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='stories')

    media = models.FileField(
        upload_to='stories/%Y/%m/%d/',
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'gif', 'mp4', 'mov'])],
    )
    media_type = models.CharField(max_length=10, choices=MEDIA_TYPE_CHOICES, default='image')
    caption = models.CharField(max_length=300, blank=True)

    # Denormalized — kept in sync by update_story_views_count below, same
    # pattern as Post.saves_count / Post.shares_count.
    views_count = models.PositiveIntegerField(default=0)

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # NOT auto_now_add — this is a fixed future timestamp set once at
    # creation, not "now" at save time. Overridable per-story (e.g. a
    # shorter-lived story) by passing expires_at explicitly on create.
    expires_at = models.DateTimeField(default=default_story_expiry, db_index=True)

    class Meta:
        db_table = 'stories'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['expires_at', 'is_deleted']),
        ]

    def __str__(self):
        return f"{self.user.username} story - {self.created_at}"

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    def soft_delete(self):
        """Mirrors CommentDeleteAPIView's pattern in comment_view.py — a
        real DB write, not just an in-memory flag flip, so it's visible to
        any other query immediately."""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at'])


class StoryView(models.Model):
    """One row per (story, viewer) pair — mirrors PostView's job for posts,
    and is what auto_expiry/analytics can query without recomputing from
    scratch."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    story = models.ForeignKey(Story, on_delete=models.CASCADE, related_name='views')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='story_views')
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'story_views'
        unique_together = ['story', 'user']
        indexes = [
            models.Index(fields=['story', '-viewed_at']),
        ]


@receiver(post_save, sender=StoryView)
@receiver(post_delete, sender=StoryView)
def update_story_views_count(sender, instance, **kwargs):
    Story.objects.filter(id=instance.story_id).update(
        views_count=StoryView.objects.filter(story_id=instance.story_id).count()
    )