# post/models.py
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
    # TASK 5 — subcategory the composer (new_post.dart) picks from the
    # dynamic `category_subcategory_map` served by the taxonomy endpoint
    # (see api_service.dart's getCategoryTaxonomy()). Deliberately a plain
    # CharField, NOT `choices=` — the valid (category -> [subcategory,...])
    # set lives in that taxonomy source and can grow without a migration;
    # hardcoding a choices list here would fight that and go stale. Cross-
    # field validation ("is this subcategory actually valid for this
    # category") belongs in PostCreateSerializer.validate(), not the model.
    subcategory = models.CharField(max_length=100, blank=True, null=True, db_index=True)

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
    # TASK 5 — this field already existed ("For scheduled posts") but
    # nothing ever set it; new_post.dart's `scheduledAt` now maps straight
    # onto it (PostCreateSerializer's `scheduled_at` field has
    # `source='published_at'`), so no new datetime column was needed.
    published_at = models.DateTimeField(blank=True, null=True, db_index=True)
    # 🔥 NEW — explicit flag rather than inferring "scheduled" from
    # `published_at > now()` at query time. A boolean + index lets the
    # publish sweep (Celery beat task, see PostCreateSerializer's
    # docstring note) do `filter(is_scheduled=True, published_at__lte=now)`
    # directly, and lets feed queries do `exclude(is_scheduled=True)`
    # without recomputing "is this in the future" per row.
    is_scheduled = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = 'posts'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at', 'is_deleted']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['category', '-created_at']),
            models.Index(fields=['category', 'subcategory', '-created_at']),
            models.Index(fields=['-likes_count', '-created_at']),  # For trending
            models.Index(fields=['is_scheduled', 'published_at']),  # For the publish sweep
        ]

    def __str__(self):
        return f"{self.user.username} - {self.category} - {self.created_at}"

    @property
    def is_due_for_publish(self):
        """True once a scheduled post's time has arrived. Used by the
        publish sweep task and can double as a defensive check anywhere a
        scheduled Post might otherwise leak into a feed query that forgot
        to filter on `is_scheduled`."""
        return self.is_scheduled and bool(self.published_at) and self.published_at <= timezone.now()

    # ⚠️ FOLLOW-UP REQUIRED OUTSIDE THIS FILE (not in scope of the files
    # provided for this task, flagging so it isn't silently forgotten):
    #   1. Every feed/listing queryset in views.py (home feed, category
    #      feed, profile feed, hashtag feed, trending, ...) needs
    #      `.exclude(is_scheduled=True)` — or, equivalently,
    #      `.filter(Q(is_scheduled=False) | Q(published_at__lte=now()))` —
    #      or a scheduled post is publicly visible the instant it's
    #      created, which defeats the whole feature. The post's own
    #      author should still be able to see/edit it before publish
    #      (e.g. a "scheduled" tab), so this is a feed-level exclusion,
    #      not a moderation_status-style global one.
    #   2. A periodic task (Celery beat, same pattern as
    #      `post.tasks.generate_video_thumbnail`) should run
    #      `Post.objects.filter(is_scheduled=True, published_at__lte=now())
    #      .update(is_scheduled=False)` on a short interval (e.g. every
    #      minute) so posts actually go live at their scheduled time
    #      instead of just being *eligible* to per (1) forever.


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
    # TASK 5 — per-attachment caption. new_post.dart collects one caption
    # per attachment (`attachment.caption`) and api_service.dart sends the
    # whole list as `media_captions` (JSON-encoded, same order/index as
    # `media_files`) — this is where PostCreateSerializer.create() now
    # writes caption[i] onto media row i.
    caption = models.CharField(max_length=500, blank=True, default='')
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


# ---------------------------------------------------------------------------
# TASK 5 — real poll support.
#
# new_post.dart already has full poll-composer UI (2-4 options, dedupe/
# empty checks) and sends it as `poll_options`: a JSON list of
# `{"text": ..., "votes": 0}` dicts. `Post.metadata` (a bare JSONField)
# could technically hold this, but that would mean no per-option vote
# counting, no "did this user already vote" enforcement, and no way to
# query/aggregate polls — all real requirements once voting is wired up,
# not just storage for what the composer submits once at create time. A
# proper one-poll-per-post + many-options + many-votes shape, same
# id/FK/db_table conventions as the rest of this file, gets us all three
# without a follow-up migration the first time voting is built.
# ---------------------------------------------------------------------------
class PostPoll(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.OneToOneField(Post, on_delete=models.CASCADE, related_name='poll')
    # Not currently sent by the client (new_post.dart has no expiry UI
    # yet) — nullable/optional so it's ready without blocking on that.
    expires_at = models.DateTimeField(blank=True, null=True)
    # Denormalized sum of all options' votes_count, same pattern as
    # Post.shares_count/saves_count above — kept in sync by
    # sync_poll_vote_counts() below rather than aggregated on every read.
    total_votes_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_polls'

    def __str__(self):
        return f"Poll on {self.post_id}"

    @property
    def is_expired(self):
        return bool(self.expires_at) and timezone.now() >= self.expires_at


class PostPollOption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    poll = models.ForeignKey(PostPoll, on_delete=models.CASCADE, related_name='options')
    text = models.CharField(max_length=200)
    votes_count = models.PositiveIntegerField(default=0)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'post_poll_options'
        ordering = ['display_order']
        indexes = [
            models.Index(fields=['poll', 'display_order']),
        ]

    def __str__(self):
        return self.text


class PostPollVote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    poll = models.ForeignKey(PostPoll, on_delete=models.CASCADE, related_name='votes')
    option = models.ForeignKey(PostPollOption, on_delete=models.CASCADE, related_name='votes')
    # 🔥 FIX (fields.E304/E305) — was related_name='poll_votes', which
    # collided with `message.PollVote.user`'s own related_name='poll_votes'
    # (that model votes on chat/group polls in the `message` app; this one
    # votes on post polls here in `post`). Both are plain FKs straight to
    # `User`, and Django requires reverse-accessor names to be unique per
    # target model across the WHOLE project — not just within one app — so
    # two unrelated apps both calling their thing "poll_votes" broke
    # `runserver`/`makemigrations`/`migrate` outright (fields.E304/E305).
    # `message.PollVote` is the older, already-wired-up feature (real
    # voting endpoint exists via MessageViewSet.poll_vote), so it keeps
    # `poll_votes`; this one is renamed instead. If any code already
    # (or in future) calls `user.poll_votes` expecting POST poll votes
    # specifically, use `user.post_poll_votes`.
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_poll_votes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'post_poll_votes'
        # One vote per user per poll — standard single-choice poll
        # behavior (matches new_post.dart's UI, which is single-select).
        # Re-voting means changing `option` on the existing row, not
        # inserting a second one; the voting endpoint (not in scope of
        # this task's files) should do get-or-update, not get_or_create.
        unique_together = ['poll', 'user']
        indexes = [
            models.Index(fields=['option']),
        ]


@receiver(post_save, sender=PostPollVote)
@receiver(post_delete, sender=PostPollVote)
def sync_poll_vote_counts(sender, instance, **kwargs):
    """Same shape as update_shares_count/update_saves_count further down:
    recompute the denormalized counters from the real rows on every
    vote/unvote/re-vote, rather than trying to +1/-1 in the view (which
    would double-count on a changed vote unless done very carefully)."""
    option_ids = list(
        PostPollOption.objects.filter(poll_id=instance.poll_id).values_list('id', flat=True)
    )
    for option_id in option_ids:
        PostPollOption.objects.filter(id=option_id).update(
            votes_count=PostPollVote.objects.filter(option_id=option_id).count()
        )
    PostPoll.objects.filter(id=instance.poll_id).update(
        total_votes_count=PostPollVote.objects.filter(poll_id=instance.poll_id).count()
    )


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


# TASK 3 — dedicated model backing the new /post/chunked/* routes
# (views.py's post_chunked_upload_init/_chunk/_complete/_status).
#
# `ChunkedUpload` above can't be reused as-is for this: it's shaped
# specifically for a comment attachment (`post_id`/`parent_id` pick the
# PostComment's target, `content` is the comment body) and has nowhere to
# park the post-creation fields (title, category, post_type, visibility,
# hashtags, location, ...) a chunked *post* upload needs to hold onto
# between `init` (when the client sends them once) and `complete` (when
# the Post row actually gets created — see PostCreateSerializer.create()
# in serializers.py for the equivalent non-chunked field set this
# mirrors).
#
# TASK 5 UPDATE: `subcategory`, `poll_options`, `is_scheduled`,
# `scheduled_at`, `media_caption` were previously left out here on
# purpose, because `Post` and `PostCreateSerializer` didn't persist them
# either — adding them here without a matching `Post` column would have
# silently dropped them a step later. Now that Task 5 added those as real
# columns (`Post.subcategory`, `Post.is_scheduled`, `Post.published_at`,
# the `PostPoll`/`PostPollOption` models, `PostMedia.caption`), this model
# is updated to match so the chunked path holds the same payload shape as
# the non-chunked one all the way through `complete()`. `media_caption` is
# singular here (one file per chunked upload) where the non-chunked path's
# `media_captions` is a list — matches the existing `media_type` (singular)
# vs. the non-chunked `media_types` (list) split already in this model.
class PostChunkedUpload(models.Model):
    upload_id = models.CharField(max_length=100, unique=True)
    file_name = models.CharField(max_length=500)
    total_chunks = models.IntegerField()
    total_size = models.BigIntegerField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    # Post-creation payload, captured once at init() and applied unchanged
    # at complete() — see PostCreateSerializer's Meta.fields for the
    # non-chunked equivalent of this set.
    title = models.CharField(max_length=300, blank=True, default='')
    content = models.TextField(blank=True, default='')
    category = models.CharField(max_length=100, default='general')
    subcategory = models.CharField(max_length=100, blank=True, null=True)
    post_type = models.CharField(max_length=20, default='video')
    visibility = models.CharField(max_length=20, default='public')
    hashtags = models.JSONField(default=list, blank=True)
    location = models.JSONField(default=dict, blank=True)
    media_caption = models.CharField(max_length=500, blank=True, default='')
    media_type = models.CharField(max_length=20, blank=True, default='')
    # Same shape client sends non-chunked: [{"text": ..., "votes": 0}, ...].
    # Applied at complete() the same way PostCreateSerializer.create()
    # applies it — see that method for the validation rules (2-4 options,
    # no duplicates) which run once, at init(), via
    # PostChunkedUploadInitSerializer reusing PostCreateSerializer's poll
    # validators rather than duplicating them.
    poll_options = models.JSONField(default=list, blank=True)
    is_scheduled = models.BooleanField(default=False)
    scheduled_at = models.DateTimeField(blank=True, null=True)

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