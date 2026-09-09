# `post` App — Complete Self-Contained Reference

Ye ek hi file hai jisme poore **post** (feed, likes, comments, saves, media)
Django app ka sara logic, code, connections, flows aur known issues cover
hain. Iske alawa kisi aur file ki zaroorat nahi — sab kuch (models →
serializers → comment_serializers → views → comment_view → urls → admin →
apps.py) yahin milega, saath me har piece kya kaam karta hai uski
explanation bhi.

---

## 1. App Overview

**App name:** `post`
**Purpose:** Full social-feed backend — create posts (text/image/video/
document/poll/carousel/link) with multi-file media, home feed (following +
trending fallback algorithm), user-post listing, post detail with view
tracking, 5-type reactions on posts, save/unsave posts + saved-list, and a
full comment system: create (regular + chunked upload up to 4GB for large
video comments), nested replies, edit, soft-delete, hide (post-owner
moderation), and 5-type comment reactions. Also serves media files with
HTTP Range support for video/audio streaming.

**Tech stack:** Django + DRF + `drf-spectacular` (OpenAPI docs) + `ffmpeg-python`
(auto video-thumbnail generation) + Django signals (denormalized counters).

**Depends on other apps:**
- `login` app's custom `User` model (`AUTH_USER_MODEL`) — needs
  `posts_count`, `is_private`, `profile_photo` fields (see that app's own
  reference doc).
- `user_profile` app's `Follow` model — imported directly
  (`from user_profile.models import Follow`) for feed personalization and
  private-account visibility checks.

**Files in this app:**
| File | Responsibility |
|---|---|
| `models.py` | `Post`, `PostMedia`, `PostLike`, `PostComment`, `CommentMedia`, `PostShare`, `PostView`, `PostSave`, `ChunkedUpload`, `CommentLike`, `Story`, `StoryView` + counter-update signals |
| `serializers.py` | Post create/list/detail serializers, reaction serializers, save serializer, Story serializers |
| `comment_serializers.py` | Comment media/user/comment serializers (with reactions), create-comment serializer |
| `views.py` | Post CRUD (create/list/detail/delete), feed, reactions, save/unsave, media streaming, Story create/list/view |
| `comment_view.py` | Comment create (regular + chunked upload), reactions, edit, delete, list, replies, hide |
| `urls.py` | URL routing for `views.py`, `comment_view.py`, and Stories |
| `admin.py` | Django admin registration (incl. Story/StoryView) |
| `apps.py` | App config (`name = 'post'`), registers `post.signals` in `ready()` |
| `services.py` | Notification hookup (post-liked/commented) + share-to-conversation helper. See §16. |
| `signals.py` | `posts_count` sync on `User` (create via signal, soft-delete via explicit call) |
| `tasks.py` | Celery: Story auto-expiry (soft-delete) + weekly hard-purge of old soft-deleted stories |
| `tests.py` | Post create/delete, reaction idempotency, comment threading, save toggle, Story expiry |
| `__init__.py` | Empty (standard package marker) |

---

## 2. ⚠️ External Dependencies Required

```python
INSTALLED_APPS = [
    ...
    'rest_framework',
    'drf_spectacular',
    'login',          # provides AUTH_USER_MODEL
    'user_profile',   # provides the Follow model used by this app
    'post',
]

AUTH_USER_MODEL = "login.User"   # must have: posts_count, is_private, profile_photo (see login app doc)

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
```

Packages needed (pip):
```
djangorestframework drf-spectacular ffmpeg-python
```
Plus the **`ffmpeg` binary itself** must be installed on the server/OS
(not just the Python wrapper) for automatic video-thumbnail generation to
work — `ffmpeg-python` just shells out to it.

Root `urls.py`:
```python
path('post/', include('post.urls')),   # or your chosen prefix — see §9
```

⚠️ `views.py` imports `from user_profile.models import Follow` directly —
this app **cannot run** unless the `user_profile` app (see its own
reference doc) is installed and migrated first.

---

## 3. `models.py` (full code)

```python
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
# trips. `update_reaction_counts` is the superset (it also sets
# like_count/confuse_count/wrong_count/imp_count/explain_count and the
# auto-flag-on-5-wrong logic), so it's the one kept.
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


# settings ya models.py me add karein
import os
import tempfile
import ffmpeg
from django.core.files import File
from django.db.models.signals import post_save
from django.dispatch import receiver


# Maan lijiye aapka PostMedia model yahan defined hai...
import os
import tempfile
import ffmpeg
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.db.models.signals import post_save
from django.dispatch import receiver


import logging as _logging

_thumb_logger = _logging.getLogger("post.thumbnails")


@receiver(post_save, sender=PostMedia)
def auto_generate_video_thumbnail(sender, instance, created, **kwargs):
    """
    Ekdum fail-safe signal jo direct DB row ko update karega bina loop crash ke.
    """
    # 1. 'video' keyword check logic robust rakhein (chahe mime_type dynamic stream ho)
    is_video = (

            instance.media_type == 'video' or
            'video' in getattr(instance, 'mime_type', '') or
            instance.file.name.lower().endswith(('.mp4', '.mov', '.avi', '.mkv'))
    )

    if created and is_video and instance.file and not instance.thumbnail:
        # FIX (post_app.md §14 issue #5): `.file.path` only exists for
        # FileSystemStorage — on S3/GCS/any remote backend this raises
        # NotImplementedError, and the old bare `except Exception` below
        # would swallow it silently via `print()` (invisible in prod
        # logs). Check up front and log properly with `logger.warning`
        # instead, so cloud-storage deployments get a clear, searchable
        # signal that thumbnails are being skipped, rather than a silent
        # no-op.
        try:
            video_input_path = instance.file.path
        except NotImplementedError:
            _thumb_logger.warning(
                "Skipping video thumbnail for PostMedia %s — storage backend "
                "doesn't support local file paths (likely S3/cloud storage). "
                "Thumbnail generation currently requires FileSystemStorage.",
                instance.id,
            )
            return

        try:
            base_name = os.path.splitext(os.path.basename(video_input_path))[0]

            # Temporary dynamic output folder construction
            temp_dir = tempfile.gettempdir()
            temp_output_path = os.path.join(temp_dir, f"{base_name}_thumb.jpg")

            # 2. FFmpeg Command to extract frame at 1st second
            (
                ffmpeg
                .input(video_input_path, ss=1.0)
                .output(temp_output_path, vframes=1)
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )

            # 3. Save thumbnail manually directly through storage layer to avoid infinite loops
            if os.path.exists(temp_output_path):
                with open(temp_output_path, 'rb') as thumb_file:
                    # File direct dynamic save paths configuration matching your format
                    thumb_name = f"posts/thumbnails/{instance.created_at.strftime('%Y/%m/%d')}/{base_name}_thumb.jpg" if hasattr(
                        instance, 'created_at') and instance.created_at else f"posts/thumbnails/{base_name}_thumb.jpg"

                    # Storage save handles directory making automatically
                    saved_path = default_storage.save(thumb_name, ContentFile(thumb_file.read()))

                    # Core loop breaker: Direct database update bypasses signals
                    PostMedia.objects.filter(id=instance.id).update(thumbnail=saved_path)

                # Dynamic os environment absolute file clean up
                if os.path.exists(temp_output_path):
                    os.remove(temp_output_path)

        except ffmpeg.Error as e:
            _thumb_logger.error(
                "FFmpeg thumbnail extraction failed for PostMedia %s — stdout: %s | stderr: %s",
                instance.id,
                e.stdout.decode("utf8") if e.stdout else "",
                e.stderr.decode("utf8") if e.stderr else "",
            )
        except Exception as e:
            _thumb_logger.error(
                "Thumbnail extraction failed for PostMedia %s: %s", instance.id, e, exc_info=True
            )


from django.db.models import Count
from django.dispatch import receiver
from django.db.models.signals import post_save, post_delete

@receiver(post_save, sender=PostLike)
@receiver(post_delete, sender=PostLike)
def update_reaction_counts(sender, instance, **kwargs):
    post_id = instance.post_id

    # 1. Sab reaction ka count ek sath nikalo
    reactions = PostLike.objects.filter(post_id=post_id).values('reaction_type').annotate(c=Count('id'))
    counts = {r['reaction_type']: r['c'] for r in reactions}

    # 2. Pehle sirf counts update karo - ye hamesha chalega
    Post.objects.filter(id=post_id).update(
        likes_count=sum(counts.values()),
        like_count=counts.get('like', 0),
        confuse_count=counts.get('confuse', 0),
        wrong_count=counts.get('wrong', 0),
        imp_count=counts.get('imp', 0),
        explain_count=counts.get('explain', 0),
    )

    # 3. Alag se flag check karo - isse upar wala fail nahi hoga
    wrong = counts.get('wrong', 0)
    if wrong >= 5:
        Post.objects.filter(id=post_id).update(moderation_status='flagged')



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
```

### Model notes
- **Two separate signal handlers update `Post.likes_count`**:
  `update_likes_count` (simple count) and `update_reaction_counts`
  (per-reaction-type breakdown, which *also* recomputes and overwrites
  `likes_count = sum(counts.values())`). Both fire on every `PostLike`
  save/delete — harmless (same final value) but redundant DB writes; could
  be merged into one handler.
- **`update_reaction_counts` auto-flags a post** (`moderation_status =
  'flagged'`) once it accumulates **5 or more `wrong` reactions** — a
  built-in lightweight community-moderation signal.
- **`auto_generate_video_thumbnail`** only fires when a `PostMedia` row is
  first `created` (not on updates), needs `instance.file.path` (so **local
  filesystem storage only** — won't work on S3/cloud storage without
  changes), and requires the `ffmpeg` binary on the host machine. Uses
  `.update()` on the queryset (not `.save()`) specifically to avoid
  re-triggering this same `post_save` signal recursively.
- **`ChunkedUpload`** is a temporary staging record for the 4GB
  chunked-upload flow (see §7) — `post_id`/`parent_id` are stored as plain
  `CharField`, not FKs (so no referential integrity check at the DB
  level; validated in the view instead).
- **`CommentLike`** mirrors `PostLike`'s 5-reaction-type structure but for
  comments, with its own simpler counter signal (`likes_count` only, no
  per-type breakdown stored on `PostComment` — the per-type breakdown is
  computed on-the-fly in serializers/views instead, see §4/§7).
- **`Story`/`StoryView`** (see §16.2 for the full backstory) — `Story.
  expires_at` is a fixed timestamp set once at creation (`default=
  default_story_expiry`, not `auto_now_add`), not recomputed on save;
  `is_expired` is a plain property, not itself a query filter, so
  listing views filter `expires_at__gt=now()` directly. `soft_delete()`
  is a real method (mirrors `CommentDeleteAPIView`'s pattern) — the
  `expire_old_stories`/`hard_delete_ancient_stories` Celery tasks (§16.2/
  `tasks.py`) call it rather than deleting rows outright. `StoryView` is
  one row per (story, viewer) — `unique_together = ['story', 'user']` —
  and its own `post_save`/`post_delete` signal keeps `Story.views_count`
  in sync, the same pattern `PostView`/`Post.views_count` uses (see
  known-issue #1 below for the one place that pattern isn't deduped).

---

## 4. `serializers.py` (full code — active version)

> The uploaded file had an entire earlier draft commented out at the top
> (dead code — Python never executes it). Only the **active code below
> it** is included here; it's what actually runs. One real fix worth
> noting: the old draft's file-size check was `file.size > 100 * 1024`
> (100 **KB**), the active version correctly checks `100 * 1024 * 1024`
> (100 **MB**).

```python
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
```

### ✅ RESOLVED: the naming collision between the two `PostCommentSerializer` classes
This section used to flag that `serializers.py` defined its **own**
`PostCommentSerializer` (simple: no media, no per-type reactions, no
camelCase fields) while `comment_view.py` (§7) imports a **completely
different** `PostCommentSerializer` from `comment_serializers.py` (§5:
has `media`, `my_reaction`/`myReaction`, `reaction_counts`/
`reactionCounts`, camelCase fields for Flutter) — a foot-gun for anyone
doing `from .serializers import PostCommentSerializer` and silently
getting the wrong one.

**Fixed:** the `serializers.py` version has been **renamed to
`PostCommentPreviewSerializer`** (see its own docstring in §4 above).
Behavior is unchanged — it's still only used internally by
`PostDetailSerializer.get_comments()` for the first 10 top-level
comments shown inline on a post's detail page (deliberately a
lightweight preview, no media/reactions), while every dedicated comment
endpoint (`comment_view.py`) still uses the full
`comment_serializers.PostCommentSerializer` — but the two classes can no
longer be confused by name.

### Serializer notes
- `get_profile_pic_url()` is a defensive helper — checks 4 possible field
  names (`profile_photo`, `profile_picture`, `avatar`, `image`) in order,
  so it degrades gracefully across different `User` model shapes.
- ✅ **Resolved:** the leftover, unused third `PostSerializer` (dead code,
  not referenced by any view) is no longer in the file.
- **NEW:** `StoryCreateSerializer` / `StorySerializer` — Story feature
  serializers (see §16.2), used by `StoryCreateAPIView`/`StoryListAPIView`
  in §6.

---

## 5. `comment_serializers.py` (full code — active version)

> Same situation as §4: an earlier draft is commented out at the top of
> the uploaded file; only the active code below is included. The real
> difference versus the draft: the active `PostCommentSerializer` adds
> **comment-level reactions** (`my_reaction`/`reaction_counts` via
> `CommentLike`), which the draft didn't have.

```python
# from rest_framework import serializers
# from .models import PostComment, CommentMedia
# from django.contrib.auth import get_user_model
#
# User = get_user_model()
#
#
# class CommentMediaSerializer(serializers.ModelSerializer):
#     # Flutter ke liye camelCase me bhi bhej rahe hain + absolute url
#     file = serializers.SerializerMethodField()
#     file_name = serializers.CharField(read_only=True)
#     file_size = serializers.IntegerField(read_only=True)
#
#     # Flutter me fileName fileSize use hota hai
#     fileName = serializers.CharField(source='file_name', read_only=True)
#     fileSize = serializers.IntegerField(source='file_size', read_only=True)
#     mimeType = serializers.CharField(source='mime_type', read_only=True)
#     mediaType = serializers.CharField(source='media_type', read_only=True)
#
#     class Meta:
#         model = CommentMedia
#         fields = ['id', 'media_type', 'mediaType', 'file', 'file_name', 'fileName', 'file_size', 'fileSize',
#                   'mime_type', 'mimeType', 'created_at']
#         read_only_fields = ['id', 'file_name', 'file_size']
#
#     def get_file(self, obj):
#         if not obj.file:
#             return None
#         request = self.context.get('request')
#         try:
#             url = obj.file.url
#             if request:
#                 return request.build_absolute_uri(url)
#             return url
#         except:
#             return str(obj.file)
#
#
# class UserShortSerializer(serializers.ModelSerializer):
#     # Tere User model me profile_photo hai
#     profile_picture = serializers.SerializerMethodField()
#     profilePicture = serializers.SerializerMethodField()  # Flutter ke liye camelCase bhi
#
#     class Meta:
#         model = User
#         fields = ['id', 'username', 'profile_picture', 'profilePicture']
#
#     def get_profile_picture(self, obj):
#         request = self.context.get('request')
#         field = None
#         if hasattr(obj, 'profile_photo') and obj.profile_photo:
#             field = obj.profile_photo
#         elif hasattr(obj, 'profile_picture') and getattr(obj, 'profile_picture', None):
#             field = obj.profile_picture
#
#         if field:
#             try:
#                 url = field.url
#                 if request:
#                     return request.build_absolute_uri(url)
#                 return url
#             except:
#                 return str(field)
#         return None
#
#     def get_profilePicture(self, obj):
#         return self.get_profile_picture(obj)
#
#
# class PostCommentSerializer(serializers.ModelSerializer):
#     user = UserShortSerializer(read_only=True)
#     media = CommentMediaSerializer(many=True, read_only=True)
#
#     # Flutter compatibility - snake + camel dono
#     likes_count = serializers.IntegerField(read_only=True)
#     replies_count = serializers.IntegerField(read_only=True)
#     likesCount = serializers.IntegerField(source='likes_count', read_only=True)
#     repliesCount = serializers.IntegerField(source='replies_count', read_only=True)
#
#     class Meta:
#         model = PostComment
#         fields = [
#             'id', 'post', 'user', 'parent', 'content', 'media',
#             'likes_count', 'likesCount', 'replies_count', 'repliesCount',
#             'is_edited', 'is_pinned', 'is_hidden', 'created_at', 'updated_at'
#         ]
#         read_only_fields = ['id', 'likes_count', 'replies_count', 'is_edited', 'created_at', 'updated_at']
#
#
# class CreateCommentSerializer(serializers.Serializer):
#     post_id = serializers.UUIDField(required=False, allow_null=True)
#     parent_id = serializers.UUIDField(required=False, allow_null=True)
#     content = serializers.CharField(required=False, allow_blank=True, default='')
#
#     def to_internal_value(self, data):
#         mutable = data.copy() if hasattr(data, 'copy') else dict(data)
#         if mutable.get('parent_id') in ['', 'string', 'null']:
#             mutable['parent_id'] = None
#         if mutable.get('post_id') in ['', 'string', 'null']:
#             mutable['post_id'] = None
#         return super().to_internal_value(mutable)
#
#     def validate(self, attrs):
#         if not attrs.get('post_id') and not attrs.get('parent_id'):
#             raise serializers.ValidationError({"post_id": "post_id is required for top-level comment"})
#         return attrs



















from rest_framework import serializers
from django.db.models import Count
from.models import PostComment, CommentMedia, CommentLike
from django.contrib.auth import get_user_model

User = get_user_model()

class CommentMediaSerializer(serializers.ModelSerializer):
    # Flutter ke liye camelCase me bhi bhej rahe hain + absolute url
    file = serializers.SerializerMethodField()
    file_name = serializers.CharField(read_only=True)
    file_size = serializers.IntegerField(read_only=True)

    # Flutter me fileName fileSize use hota hai
    fileName = serializers.CharField(source='file_name', read_only=True)
    fileSize = serializers.IntegerField(source='file_size', read_only=True)
    mimeType = serializers.CharField(source='mime_type', read_only=True)
    mediaType = serializers.CharField(source='media_type', read_only=True)

    class Meta:
        model = CommentMedia
        fields = ['id', 'media_type', 'mediaType', 'file', 'file_name', 'fileName', 'file_size', 'fileSize',
                  'mime_type', 'mimeType', 'created_at']
        read_only_fields = ['id', 'file_name', 'file_size']

    def get_file(self, obj):
        if not obj.file:
            return None
        request = self.context.get('request')
        try:
            url = obj.file.url
            if request:
                return request.build_absolute_uri(url)
            return url
        except:
            return str(obj.file)

class UserShortSerializer(serializers.ModelSerializer):
    # Tere User model me profile_photo hai
    profile_picture = serializers.SerializerMethodField()
    profilePicture = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'profile_picture', 'profilePicture']

    def get_profile_picture(self, obj):
        request = self.context.get('request')
        field = None
        if hasattr(obj, 'profile_photo') and obj.profile_photo:
            field = obj.profile_photo
        elif hasattr(obj, 'profile_picture') and getattr(obj, 'profile_picture', None):
            field = obj.profile_picture

        if field:
            try:
                url = field.url
                if request:
                    return request.build_absolute_uri(url)
                return url
            except:
                return str(field)
        return None

    def get_profilePicture(self, obj):
        return self.get_profile_picture(obj)

class PostCommentSerializer(serializers.ModelSerializer):
    user = UserShortSerializer(read_only=True)
    media = CommentMediaSerializer(many=True, read_only=True)

    # Flutter compatibility - snake + camel dono
    likes_count = serializers.IntegerField(read_only=True)
    replies_count = serializers.IntegerField(read_only=True)
    likesCount = serializers.IntegerField(source='likes_count', read_only=True)
    repliesCount = serializers.IntegerField(source='replies_count', read_only=True)

    # NEW - Comment reaction like post
    my_reaction = serializers.SerializerMethodField()
    myReaction = serializers.SerializerMethodField()
    reaction_counts = serializers.SerializerMethodField()
    reactionCounts = serializers.SerializerMethodField()

    class Meta:
        model = PostComment
        fields = [
            'id', 'post', 'user', 'parent', 'content', 'media',
            'likes_count', 'likesCount', 'replies_count', 'repliesCount',
            'is_edited', 'is_pinned', 'is_hidden', 'created_at', 'updated_at',
            'my_reaction', 'myReaction', 'reaction_counts', 'reactionCounts'
        ]
        read_only_fields = ['id', 'likes_count', 'replies_count', 'is_edited', 'created_at', 'updated_at']

    def get_my_reaction(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            like = CommentLike.objects.filter(comment=obj, user=request.user).first()
            return like.reaction_type if like else None
        return None

    def get_myReaction(self, obj):
        return self.get_my_reaction(obj)

    def get_reaction_counts(self, obj):
        qs = CommentLike.objects.filter(comment=obj).values('reaction_type').annotate(c=Count('id'))
        counts = {r['reaction_type']: r['c'] for r in qs}
        return {
            'like': counts.get('like', 0),
            'confuse': counts.get('confuse', 0),
            'wrong': counts.get('wrong', 0),
            'imp': counts.get('imp', 0),
            'explain': counts.get('explain', 0),
            'total': sum(counts.values())
        }

    def get_reactionCounts(self, obj):
        return self.get_reaction_counts(obj)

class CreateCommentSerializer(serializers.Serializer):
    post_id = serializers.UUIDField(required=False, allow_null=True)
    parent_id = serializers.UUIDField(required=False, allow_null=True)
    content = serializers.CharField(required=False, allow_blank=True, default='')

    def to_internal_value(self, data):
        mutable = data.copy() if hasattr(data, 'copy') else dict(data)
        if mutable.get('parent_id') in ['', 'string', 'null']:
            mutable['parent_id'] = None
        if mutable.get('post_id') in ['', 'string', 'null']:
            mutable['post_id'] = None
        return super().to_internal_value(mutable)

    def validate(self, attrs):
        if not attrs.get('post_id') and not attrs.get('parent_id'):
            raise serializers.ValidationError({"post_id": "post_id is required for top-level comment"})
        return attrs
```

### Notes
- `CreateCommentSerializer.to_internal_value()` treats the literal
  strings `'string'` and `'null'` as "empty" — this is a Swagger/OpenAPI-UI
  quirk workaround (drf-spectacular's "Try it out" form sends the literal
  placeholder text `"string"` if a field is left untouched); needed so
  testing via the docs UI doesn't accidentally create a comment with
  `post_id="string"`.
- `get_reaction_counts()` / `get_reactionCounts()` duplicate the exact
  same aggregation logic as `comment_react()` in `comment_view.py` (§7) —
  three separate places compute this shape. Fine functionally, but if the
  5 reaction types ever change, all three spots need updating together.

---

## 6. `views.py` (full code)

```python
#post/views.py
import os
import re
import logging
import mimetypes
from collections import Counter
from datetime import timedelta

from django.db import transaction
from django.db.models import Q, F, Case, When, IntegerField, FloatField, ExpressionWrapper
from django.utils import timezone
from django.http import StreamingHttpResponse, Http404
from django.conf import settings
from django.contrib.auth import get_user_model

from rest_framework import status, parsers, generics, filters
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.pagination import PageNumberPagination

from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from drf_spectacular.types import OpenApiTypes

from.models import Post, PostMedia, PostView, PostLike, PostSave, PostComment, Story, StoryView
from.serializers import (
    PostCreateSerializer,
    PostListSerializer,
    PostDetailSerializer,
    PostMediaSerializer,
    PostSaveSerializer,
    StoryCreateSerializer,
    StorySerializer,
)
from.signals import decrement_posts_count_on_soft_delete
from user_profile.models import Follow

# Local import for reaction
from rest_framework import serializers as drf_serializers

User = get_user_model()
logger = logging.getLogger(__name__)

# ===================== PAGINATION =====================
class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

class HomeFeedPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 50

# ===================== POST CREATE =====================
class PostCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]
    serializer_class = PostCreateSerializer

    @extend_schema(
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'title': {'type': 'string', 'maxLength': 255},
                    'content': {'type': 'string'},
                    'category': {
                        'type': 'string',
                        'enum': ['general', 'tech', 'jobs', 'news', 'education',
                                'business', 'entertainment', 'sports', 'lifestyle', 'other']
                    },
                    'post_type': {
                        'type': 'string',
                        'enum': ['text', 'image', 'video', 'document', 'poll', 'article', 'carousel', 'link']
                    },
                    'visibility': {
                        'type': 'string',
                        'enum': ['public', 'connections', 'private']
                    },
                    'hashtags': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Hashtags without #'
                    },
                    'mentioned_user_ids': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'UUIDs of mentioned users'
                    },
                    'metadata': {'type': 'object'},
                    'location': {'type': 'object'},
                    'media_files': {
                        'type': 'array',
                        'items': {'type': 'string', 'format': 'binary'},
                    },
                    'media_types': {
                        'type': 'array',
                        'items': {
                            'type': 'string',
                            'enum': ['image', 'video', 'document', 'audio', 'gif']
                        },
                    }
                },
                'required': ['post_type', 'category']
            }
        },
        responses={201: PostCreateSerializer},
        description='Create post with multiple media files. Max 10 files, 100MB each.'
    )
    @transaction.atomic
    def post(self, request):
        serializer = PostCreateSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            try:
                post = serializer.save(user=request.user)
                # 🔥 FIX: posts_count increment moved to signals.py
                # (increment_posts_count_on_create, post_save on Post).
                # Doing it here too would double-count — see signals.py's
                # module docstring for why the signal is now the single
                # source of truth for this counter.
                logger.info(f'Post created: {post.id} by {request.user.id}')
                output_serializer = PostCreateSerializer(post, context={'request': request})
                return Response({"success": True, "message": "Post created successfully","data": output_serializer.data}, status=status.HTTP_201_CREATED)
            except Exception as e:
                logger.error(f'Post creation failed: {e}', exc_info=True)
                return Response({"success": False, "message": "Failed to create post"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({"success": False, "message": "Validation failed","errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

# ===================== HOME FEED =====================
class HomeFeedView(generics.ListAPIView):
    serializer_class = PostListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = HomeFeedPagination
    def get_serializer_context(self):
        return {'request': self.request}
    def get_queryset(self):
        request_user = self.request.user
        following_ids = Follow.objects.filter(follower=request_user,status=Follow.Status.ACCEPTED).values_list('following_id', flat=True)
        seven_days_ago = timezone.now() - timedelta(days=7)
        base_qs = Post.objects.select_related('user').prefetch_related('media').filter(is_deleted=False,moderation_status='approved',is_sensitive=False).exclude(user=request_user)
        if following_ids.exists():
            following_posts = base_qs.filter(user_id__in=following_ids,visibility__in=['public', 'connections']).annotate(
                is_recent=Case(When(created_at__gte=seven_days_ago, then=1),default=0,output_field=IntegerField()),
                engagement_score=ExpressionWrapper(F('likes_count')*3.0+F('comments_count')*5.0+F('shares_count')*10.0+F('views_count')*0.1,output_field=FloatField())
            ).order_by('-is_recent', '-engagement_score', '-created_at')
            if following_posts.exists():
                return following_posts
        return base_qs.filter(visibility='public',created_at__gte=seven_days_ago).annotate(
            engagement_score=ExpressionWrapper(F('likes_count')*3.0+F('comments_count')*5.0+F('shares_count')*10.0,output_field=FloatField())
        ).order_by('-engagement_score', '-views_count', '-created_at')

# ===================== USER POSTS LIST =====================
class PostListAPIView(generics.ListAPIView):
    serializer_class = PostListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'content', 'hashtags']
    ordering_fields = ['created_at', 'likes_count', 'views_count']
    ordering = ['-created_at']
    def get_serializer_context(self):
        return {'request': self.request}
    @extend_schema(
        parameters=[
            OpenApiParameter(name='target_user_id', type=str, description='User UUID'),
            OpenApiParameter(name='category', type=str, description='Filter by category'),
            OpenApiParameter(name='post_type', type=str, description='Filter by type'),
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
    def get_queryset(self):
        request_user = self.request.user
        target_user_id = self.request.query_params.get('target_user_id')
        base_qs = Post.objects.select_related('user').prefetch_related('media').filter(is_deleted=False,moderation_status='approved')
        if not target_user_id or str(request_user.id) == target_user_id:
            return base_qs.filter(user=request_user).order_by('-created_at')
        try:
            target_user = User.objects.get(id=target_user_id)
        except User.DoesNotExist:
            return Post.objects.none()
        is_following = Follow.objects.filter(follower=request_user,following=target_user,status=Follow.Status.ACCEPTED).exists()
        if target_user.is_private and not is_following:
            return Post.objects.none()
        queryset = base_qs.filter(user=target_user)
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)
        post_type = self.request.query_params.get('post_type')
        if post_type:
            queryset = queryset.filter(post_type=post_type)
        if is_following:
            return queryset.filter(Q(visibility='public') | Q(visibility='connections')).order_by('-created_at')
        return queryset.filter(visibility='public').order_by('-created_at')

# ===================== POST DETAIL =====================
class PostDetailAPIView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PostDetailSerializer
    queryset = Post.objects.select_related('user').prefetch_related('media','comments__user','comments__replies')
    lookup_field = 'id'
    def get_serializer_context(self):
        return {'request': self.request}
    @extend_schema(description='Get single post with comments and view tracking')
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.is_deleted:
            return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
        if instance.visibility == 'private' and instance.user!= request.user:
            return Response({"success": False, "message": "Post is private"}, status=status.HTTP_403_FORBIDDEN)
        if instance.visibility == 'connections':
            is_following = Follow.objects.filter(follower=request.user,following=instance.user,status=Follow.Status.ACCEPTED).exists()
            if not is_following and instance.user!= request.user:
                return Response({"success": False, "message": "Only connections can view"}, status=status.HTTP_403_FORBIDDEN)
        # FIX (post_app.md §14 issue #1): `views_count` used to increment
        # unconditionally on every request, even repeat visits by the same
        # user, even though `PostView` itself was already correctly deduped
        # via get_or_create. Now the counter only moves on a genuinely new
        # (post, user) pair — "unique viewers" semantics, matching what
        # `PostView`'s own uniqueness already implies.
        _, is_new_view = PostView.objects.get_or_create(post=instance, user=request.user)
        if is_new_view:
            Post.objects.filter(id=instance.id).update(views_count=F('views_count') + 1)
        serializer = self.get_serializer(instance)
        return Response({"success": True,"data": serializer.data})

# ===================== POST DELETE =====================
# NEW — checklist item 57 ("create/list/delete Post") mentioned delete but
# no such endpoint existed anywhere in urls.py/views.py. Soft-delete only,
# author-or-staff, mirroring CommentDeleteAPIView's pattern exactly
# (is_deleted/deleted_at, never a real row removal).
class PostDeleteAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Soft-delete a post", tags=["Post"])
    @transaction.atomic
    def delete(self, request, id):
        from django.shortcuts import get_object_or_404

        post = get_object_or_404(Post, id=id, is_deleted=False)
        if post.user_id != request.user.id and not request.user.is_staff:
            return Response({"success": False, "message": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)

        post.is_deleted = True
        post.deleted_at = timezone.now()
        post.save(update_fields=["is_deleted", "deleted_at"])
        decrement_posts_count_on_soft_delete(post)

        return Response({"success": True, "message": "Post deleted"}, status=status.HTTP_204_NO_CONTENT)


# ===================== STORIES =====================
# NEW — checklist items 54/55/57/60. Story model added in models.py.
class StoryCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(summary="Create a Story (24h auto-expiry)", tags=["Stories"])
    def post(self, request):
        serializer = StoryCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        story = serializer.save()
        return Response(StorySerializer(story, context={"request": request}).data, status=status.HTTP_201_CREATED)


class StoryListAPIView(generics.ListAPIView):
    """Active (non-expired, non-deleted) stories from people the requester
    follows, plus their own. Expiry is enforced here in real time — the
    `expire_old_stories` celery task (tasks.py) only does housekeeping
    (soft-deleting rows so they don't pile up), it isn't what makes an
    expired story stop showing up."""
    serializer_class = StorySerializer
    permission_classes = [IsAuthenticated]

    def get_serializer_context(self):
        return {"request": self.request}

    def get_queryset(self):
        request_user = self.request.user
        following_ids = Follow.objects.filter(
            follower=request_user, status=Follow.Status.ACCEPTED
        ).values_list("following_id", flat=True)
        return (
            Story.objects.select_related("user")
            .filter(is_deleted=False, expires_at__gt=timezone.now())
            .filter(Q(user_id__in=following_ids) | Q(user=request_user))
            .order_by("user_id", "-created_at")
        )


class StoryViewAPIView(APIView):
    """Records a view (deduped per user via unique_together) and returns
    the current view count — mirrors PostDetailAPIView's PostView tracking."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Mark a story as viewed", tags=["Stories"])
    def post(self, request, story_id):
        from django.shortcuts import get_object_or_404

        story = get_object_or_404(Story, id=story_id, is_deleted=False)
        if story.is_expired:
            return Response({"success": False, "message": "Story expired"}, status=status.HTTP_404_NOT_FOUND)

        StoryView.objects.get_or_create(story=story, user=request.user)
        story.refresh_from_db(fields=["views_count"])
        return Response({"success": True, "views_count": story.views_count})


# ===================== MEDIA SERVE WITH RANGE =====================
def serve_media_with_range(request, path):
    file_path = os.path.join(settings.MEDIA_ROOT, path)
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        raise Http404("File not found")
    if '..' in path or path.startswith('/'):
        raise Http404("Invalid path")
    content_type, _ = mimetypes.guess_type(file_path)
    content_type = content_type or 'application/octet-stream'
    file_size = os.path.getsize(file_path)
    file_name = os.path.basename(file_path)
    range_header = request.META.get('HTTP_RANGE', '')
    if range_header and (content_type.startswith('video/') or content_type.startswith('audio/')):
        range_match = re.match(r'bytes=(\d+)-(\d*)', range_header)
        if range_match:
            first_byte = int(range_match.group(1))
            last_byte = int(range_match.group(2)) if range_match.group(2) else file_size - 1
            if first_byte >= file_size:
                return StreamingHttpResponse(status=416)
            length = last_byte - first_byte + 1
            def file_gen():
                with open(file_path, 'rb') as f:
                    f.seek(first_byte)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(remaining, 65536))
                        if not chunk:
                            break
                        yield chunk
                        remaining -= len(chunk)
            response = StreamingHttpResponse(file_gen(), status=206, content_type=content_type)
            response['Content-Range'] = f'bytes {first_byte}-{last_byte}/{file_size}'
            response['Content-Length'] = str(length)
            response['Accept-Ranges'] = 'bytes'
            response['Content-Disposition'] = f'inline; filename="{file_name}"'
            response['Cache-Control'] = 'public, max-age=31536000'
            return response
    def file_gen():
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                yield chunk
    response = StreamingHttpResponse(file_gen(), content_type=content_type)
    response['Content-Length'] = str(file_size)
    response['Accept-Ranges'] = 'bytes'
    doc_extensions = ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.zip', '.rar', '.txt']
    is_doc = any(file_name.lower().endswith(ext) for ext in doc_extensions)
    if content_type == 'application/pdf':
        response['Content-Disposition'] = f'inline; filename="{file_name}"'
    elif is_doc:
        response['Content-Disposition'] = f'attachment; filename="{file_name}"'
        response['Content-Type'] = 'application/octet-stream'
    elif content_type.startswith(('image/', 'video/', 'audio/')):
        response['Content-Disposition'] = f'inline; filename="{file_name}"'
        response['Cache-Control'] = 'public, max-age=31536000'
    else:
        response['Content-Disposition'] = f'attachment; filename="{file_name}"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response

# ===================== REACTION API - NEW FUNCTION ADDED =====================
# NOTE (post_app.md §14 issue #9 — NOT auto-fixed): a second
# `ReactionRequestSerializer` reportedly also exists in `serializers.py`.
# I didn't consolidate this automatically because I haven't seen that
# file's version of the class — if its `choices` list or field name ever
# drifts from this one, blindly deleting one copy could silently change
# validation behavior. Compare the two definitions once you have both
# files open; if identical, delete this local copy and instead do
# `from .serializers import ReactionRequestSerializer` up top (and drop
# the now-unused `from rest_framework import serializers as
# drf_serializers` import if nothing else in this file uses it).
class ReactionRequestSerializer(drf_serializers.Serializer):
    reaction = drf_serializers.ChoiceField(choices=['like','confuse','wrong','imp','explain'])

class PostReactionAPIView(APIView):
    permission_classes = [IsAuthenticated]
    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    @extend_schema(
        summary="Toggle Reaction - Like / Unlike",
        description="Like: count +1, Same reaction dubara -> Unlike count -1, Alag reaction -> change",
        request=ReactionRequestSerializer,
        responses={200: OpenApiTypes.OBJECT},
        tags=["Post Reactions"]
    )
    def post(self, request, post_id):
        from django.shortcuts import get_object_or_404
        post = get_object_or_404(Post, id=post_id)
        serializer = ReactionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reaction_type = serializer.validated_data['reaction']
        user = request.user
        existing = PostLike.objects.filter(post=post, user=user).first()
        if existing:
            if existing.reaction_type == reaction_type:
                existing.delete() # UNLIKE -> count auto -1 by signal
                status_msg = "unliked"
                my_reaction = None
            else:
                existing.reaction_type = reaction_type
                existing.save() # CHANGE -> signal handle
                status_msg = "changed"
                my_reaction = reaction_type
        else:
            PostLike.objects.create(post=post, user=user, reaction_type=reaction_type) # LIKE -> +1
            status_msg = "liked"
            my_reaction = reaction_type

        post.refresh_from_db()
        return Response({
            "status": status_msg,
            "my_reaction": my_reaction,
            "counts": {
                "like": post.like_count,
                "confuse": post.confuse_count,
                "wrong": post.wrong_count,
                "imp": post.imp_count,
                "explain": post.explain_count,
                "total": post.likes_count,
            }
        })

    @extend_schema(summary="Get Reaction Counts", tags=["Post Reactions"])
    def get(self, request, post_id):
        from django.shortcuts import get_object_or_404
        post = get_object_or_404(Post, id=post_id)
        my_reaction = None
        if request.user.is_authenticated:
            obj = PostLike.objects.filter(post=post, user=request.user).first()
            if obj:
                my_reaction = obj.reaction_type
        return Response({
            "post_id": str(post.id),
            "counts": {
                "like": post.like_count,
                "confuse": post.confuse_count,
                "wrong": post.wrong_count,
                "imp": post.imp_count,
                "explain": post.explain_count,
                "total": post.likes_count,
            },
            "my_reaction": my_reaction
        })

from django.shortcuts import get_object_or_404

# ===================== POST SAVE / UNSAVE TOGGLE =====================
class PostSaveToggleAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Save / Unsave Post - Toggle",
        description="Pehli baar call -> Saved, Dubara call -> Unsaved",
        request=OpenApiTypes.OBJECT,
        tags=["Post Save"]
    )
    def post(self, request, post_id):
        post = get_object_or_404(Post, id=post_id, is_deleted=False)
        collection_name = request.data.get('collection_name', 'default')

        # Check pehle se saved hai kya?
        saved_obj = PostSave.objects.filter(post=post, user=request.user).first()

        if saved_obj:
            # Already saved hai -> ab unsave karo
            saved_obj.delete()
            post.refresh_from_db()
            return Response({
                "status": "unsaved",
                "is_saved": False,
                "saves_count": post.saves_count
            }, status=status.HTTP_200_OK)
        else:
            # Save karo
            PostSave.objects.create(
                post=post,
                user=request.user,
                collection_name=collection_name
            )
            post.refresh_from_db()
            return Response({
                "status": "saved",
                "is_saved": True,
                "saves_count": post.saves_count
            }, status=status.HTTP_201_CREATED)

# ===================== SAVED POSTS LIST =====================
class SavedPostsListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PostListSerializer
    pagination_class = StandardResultsSetPagination

    def get_serializer_context(self):
        return {'request': self.request}

    @extend_schema(
        summary="Mere Saved Posts ki List",
        parameters=[
            OpenApiParameter(name='collection_name', type=str, required=False, description='Filter by collection'),
        ],
        tags=["Post Save"]
    )
    def get_queryset(self):
        user = self.request.user
        qs = Post.objects.select_related('user').prefetch_related('media').filter(
            saved_by__user=user,
            is_deleted=False
        ).order_by('-saved_by__created_at')

        collection = self.request.query_params.get('collection_name')
        if collection:
            qs = qs.filter(saved_by__collection_name=collection)
        return qs

# ===================== HASHTAG DISCOVERY (checklist: "Hashtag") =====================
# post_app.md's overview table lists Hashtag as one of this app's core
# responsibilities, but until now `Post.hashtags` (models.py) was
# write-only — populated on create, never queried back out. These two
# views are what actually make it a discovery feature instead of just
# metadata sitting on the row.
class HashtagPostsAPIView(generics.ListAPIView):
    """GET /post/hashtag/<tag>/ — public posts carrying that hashtag.

    Relies on PostCreateSerializer normalizing hashtags to lowercase at
    write time (serializers.py fix) — without that, `hashtags__contains`
    below would miss posts whose tag casing didn't happen to match.

    `hashtags__contains=[tag]` is a JSONField containment lookup:
    native on Postgres (jsonb `@>`), and supported on SQLite via the
    JSON1 extension on Django ≥3.1 — if this app ends up on an older
    SQLite without JSON1, swap this for a `Q(hashtags__icontains=tag)`
    fallback (less precise — can substring-match inside a longer tag).
    """
    serializer_class = PostListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_serializer_context(self):
        return {'request': self.request}

    @extend_schema(
        summary="Posts by hashtag",
        parameters=[OpenApiParameter(name='tag', type=str, location=OpenApiParameter.PATH)],
        tags=["Hashtag"],
    )
    def get_queryset(self):
        tag = self.kwargs['tag'].strip().lstrip('#').lower()
        return Post.objects.select_related('user').prefetch_related('media').filter(
            is_deleted=False, moderation_status='approved', visibility='public',
            hashtags__contains=[tag],
        ).annotate(
            engagement_score=ExpressionWrapper(
                F('likes_count') * 3.0 + F('comments_count') * 5.0 + F('shares_count') * 10.0,
                output_field=FloatField(),
            )
        ).order_by('-engagement_score', '-created_at')


class TrendingHashtagsAPIView(APIView):
    """GET /post/hashtags/trending/?days=7&limit=20

    ⚠️ Application-level counting over a bounded recent sample, not a
    real SQL aggregation — JSONField list elements aren't natively
    GROUP-BY-able portably across Postgres/SQLite. Same "note it, don't
    block on it" call as checklist item 60 made for feed fan-out: fine
    at this app's current scale (bounded to the most recent 2000 public
    posts in the window), replace with a Redis sorted set incremented
    in PostCreateSerializer.create() once volume actually demands it.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Trending hashtags",
        parameters=[
            OpenApiParameter(name='days', type=int, required=False, description='Lookback window, default 7'),
            OpenApiParameter(name='limit', type=int, required=False, description='Max tags returned, default 20'),
        ],
        tags=["Hashtag"],
    )
    def get(self, request):
        days = int(request.query_params.get('days', 7))
        limit = min(int(request.query_params.get('limit', 20)), 50)
        since = timezone.now() - timedelta(days=days)

        recent_hashtag_lists = Post.objects.filter(
            is_deleted=False, moderation_status='approved', visibility='public',
            created_at__gte=since,
        ).exclude(hashtags=[]).order_by('-created_at').values_list('hashtags', flat=True)[:2000]

        counts = Counter()
        for tags in recent_hashtag_lists:
            counts.update(tags)

        results = [{"hashtag": tag, "count": count} for tag, count in counts.most_common(limit)]
        return Response({"success": True, "days": days, "results": results})


# ===================== EXPLORE / DISCOVER (checklist: "Explore-content") =====================
class ExploreFeedAPIView(generics.ListAPIView):
    """GET /post/explore/?category=...

    HomeFeedView (above) already has a public-posts fallback branch for
    when a user follows nobody / their follows haven't posted recently
    — but that only ever surfaces as a fallback *inside* the following
    feed. There was no standalone discovery surface a user could open
    any time regardless of who they follow (the "Explore" grid pattern)
    — this is that endpoint.

    Deliberately excludes the requester's own posts AND posts from
    accounts they already follow, so Explore stays genuinely about
    finding new accounts rather than duplicating the home feed.
    """
    serializer_class = PostListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = HomeFeedPagination

    def get_serializer_context(self):
        return {'request': self.request}

    @extend_schema(
        summary="Explore / discover public posts",
        parameters=[OpenApiParameter(name='category', type=str, required=False)],
        tags=["Explore"],
    )
    def get_queryset(self):
        request_user = self.request.user
        following_ids = Follow.objects.filter(
            follower=request_user, status=Follow.Status.ACCEPTED
        ).values_list('following_id', flat=True)

        thirty_days_ago = timezone.now() - timedelta(days=30)
        qs = Post.objects.select_related('user').prefetch_related('media').filter(
            is_deleted=False, moderation_status='approved', is_sensitive=False, visibility='public',
        ).exclude(user=request_user).exclude(user_id__in=following_ids)

        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)

        recent_qs = qs.filter(created_at__gte=thirty_days_ago).annotate(
            engagement_score=ExpressionWrapper(
                F('likes_count') * 3.0 + F('comments_count') * 5.0 + F('shares_count') * 10.0 + F('views_count') * 0.1,
                output_field=FloatField(),
            )
        ).order_by('-engagement_score', '-created_at')

        # Same defensive fallback pattern as HomeFeedView: a brand-new
        # platform / a narrow category filter can easily have zero posts
        # in the last 30 days — fall back to all-time top public posts
        # rather than showing an empty grid.
        if recent_qs.exists():
            return recent_qs
        return qs.annotate(
            engagement_score=ExpressionWrapper(
                F('likes_count') * 3.0 + F('comments_count') * 5.0 + F('shares_count') * 10.0 + F('views_count') * 0.1,
                output_field=FloatField(),
            )
        ).order_by('-engagement_score', '-created_at')
```

### View notes
- `PostReactionAPIView` defines a **local** `ReactionRequestSerializer`
  identical in shape to the one already in `serializers.py` (§4). Since
  `views.py` only imports specific names (not `import *`), there's no
  actual name collision at runtime — but it's duplicated logic worth
  consolidating.
- `serve_media_with_range()` is a plain Django view function (not DRF) —
  wired in `urls.py` only when `settings.DEBUG` is `True` (see §8). In
  production you'd typically serve `/media/` via nginx/S3/CDN directly
  instead of through Django.

---

## 7. `comment_view.py` (full code — active version)

> Same as §4/§5: an earlier draft is commented out at the top of the
> uploaded file. One real **behavior difference** worth flagging: the old
> draft blocked replying to a reply (`"Only 1 level nesting allowed"`
> error if `parent.parent_id is not None`) — the active version below has
> **removed that restriction**, so replies can now nest arbitrarily deep
> ("FIX 2 - Multiple nesting allow kar diya" comment in the source).

```python
#post/comment_view.py
# NOTE (cleanup, post_app.md — dead code, not a "known issue" but worth
# flagging): the original file had ~326 lines of an OLDER, fully
# commented-out version of everything below (CommentCreateAPIView,
# chunked_upload_init/_chunk/_complete, CommentUpdateAPIView,
# CommentDeleteAPIView, CommentListAPIView, CommentRepliesAPIView,
# CommentHideAPIView) sitting above the real, active definitions of the
# exact same classes/functions. Python just skips commented lines, so it
# was harmless at runtime -- but it's confusing for anyone reading the
# file cold, bloats git-blame/diffs, and risks someone editing the DEAD
# copy by mistake thinking they fixed something. Removed here; the live
# code below is untouched and behaves identically to before.
import os
import uuid
import shutil
from django.conf import settings
from django.db import transaction
from django.db.models import F, Count
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from drf_spectacular.utils import extend_schema

from.models import Post, PostComment, CommentMedia, ChunkedUpload, CommentLike
from.comment_serializers import CreateCommentSerializer, PostCommentSerializer

def get_media_type(file):
    content_type = getattr(file, 'content_type', '') or ''
    name = (getattr(file, 'name', '') or '').lower()
    if content_type.startswith('image/') or name.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.heic')):
        return 'image'
    if content_type.startswith('video/') or name.endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
        return 'video'
    if content_type.startswith('audio/') or name.endswith(('.mp3', '.wav', '.m4a', '.ogg', '.aac', '.opus')):
        return 'audio'
    if name.endswith(('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.zip', '.txt', '.csv')):
        return 'document'
    return 'other'

class CommentCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(summary="Create Comment or Reply - Any file upto 4GB", tags=['Comments'])
    @transaction.atomic
    def post(self, request):
        data_dict = {}
        for key in request.data.keys():
            if key!= 'files':
                data_dict[key] = request.data.get(key)

        if data_dict.get('parent_id') in ['', 'string', 'null', 'None', None]:
            data_dict['parent_id'] = None
        if data_dict.get('post_id') in ['', 'string', 'null', None]:
            data_dict['post_id'] = None

        serializer = CreateCommentSerializer(data=data_dict)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        parent = None
        if data.get('parent_id'):
            parent = get_object_or_404(PostComment, id=data['parent_id'], is_deleted=False)
            post = parent.post
            # FIX 2 - Multiple nesting allow kar diya, ye block hata diya
            # if parent.parent_id is not None:
            # return Response({"error": "Only 1 level nesting allowed"}, status=400)
        else:
            post = get_object_or_404(Post, id=data['post_id'])

        if post.is_comments_disabled:
            return Response({"error": "Comments disabled"}, status=403)

        files = [f for f in request.FILES.getlist('files') if hasattr(f, 'size')]
        if not files:
            files = [f for f in request.FILES.getlist('file') if hasattr(f, 'size')]

        content = data.get('content', '').strip()
        if not content and not files:
            return Response({"error": "Content or file is required"}, status=400)

        comment = PostComment.objects.create(post=post, user=request.user, parent=parent, content=content)

        for f in files[:5]:
            CommentMedia.objects.create(
                comment=comment,
                media_type=get_media_type(f),
                file=f,
                file_size=f.size,
                mime_type=getattr(f, 'content_type', '')
            )

        if parent:
            PostComment.objects.filter(id=parent.id).update(replies_count=F('replies_count') + 1)
        else:
            Post.objects.filter(id=post.id).update(comments_count=F('comments_count') + 1)

        return Response(PostCommentSerializer(comment, context={'request': request}).data, status=201)

# ================= 4GB CHUNKED UPLOAD =================
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([JSONParser, MultiPartParser, FormParser])
def chunked_upload_init(request):
    try:
        file_name = request.data.get('file_name')
        total_chunks = int(request.data.get('total_chunks', 0))
        total_size = int(request.data.get('total_size', 0))
        post_id = request.data.get('post_id')
        parent_id = request.data.get('parent_id')

        if parent_id in ['', 'null', 'None']:
            parent_id = None

        if total_size > 4294967296:
            return Response({"error": "File too large. Max 4GB allowed"}, status=400)

        if not file_name or (not post_id and not parent_id) or total_chunks == 0:
            return Response({"error": "file_name, post_id, total_chunks required"}, status=400)

        # FIX (post_app.md §14 issue #10): the regular CommentCreateAPIView
        # already blocks new comments on a post with `is_comments_disabled`,
        # but this chunked-upload path (used for large video comments)
        # never checked it — someone could still start (and finish) a 4GB
        # video-comment upload on a post whose owner explicitly disabled
        # comments. Checked here, at `init` time, so a blocked upload fails
        # immediately instead of after the client has already spent time/
        # bandwidth pushing chunks.
        if parent_id:
            parent_comment = get_object_or_404(PostComment, id=parent_id, is_deleted=False)
            target_post = parent_comment.post
        else:
            target_post = get_object_or_404(Post, id=post_id)
        if target_post.is_comments_disabled:
            return Response({"error": "Comments disabled"}, status=403)

        upload_id = str(uuid.uuid4())
        ChunkedUpload.objects.create(
            upload_id=upload_id,
            file_name=file_name,
            total_chunks=total_chunks,
            total_size=total_size,
            post_id=post_id if post_id else None,
            parent_id=parent_id,
            content=request.data.get('content', ''),
            user=request.user
        )
        os.makedirs(os.path.join(settings.MEDIA_ROOT, 'temp_chunks', upload_id), exist_ok=True)
        return Response({"upload_id": upload_id, "message": "Ready for chunks"}, status=200)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({"error": str(e)}, status=400)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def chunked_upload_chunk(request):
    try:
        upload_id = request.data.get('upload_id')
        chunk_index = request.data.get('chunk_index')
        chunk_file = request.FILES.get('chunk')

        if not upload_id or chunk_file is None:
            return Response({"error": "upload_id and chunk file required"}, status=400)

        try:
            chunk_index = int(chunk_index)
        except:
            return Response({"error": "chunk_index must be int"}, status=400)

        upload = get_object_or_404(ChunkedUpload, upload_id=upload_id, user=request.user)

        chunk_dir = os.path.join(settings.MEDIA_ROOT, 'temp_chunks', upload_id)
        os.makedirs(chunk_dir, exist_ok=True)
        chunk_path = os.path.join(chunk_dir, f'chunk_{chunk_index}')

        with open(chunk_path, 'wb') as f:
            for c in chunk_file.chunks():
                f.write(c)

        progress = int(((chunk_index + 1) / upload.total_chunks) * 100)
        return Response({"received": chunk_index, "progress": progress}, status=200)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({"error": str(e)}, status=400)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([JSONParser, MultiPartParser, FormParser])
@transaction.atomic
def chunked_upload_complete(request):
    try:
        upload_id = request.data.get('upload_id')
        if not upload_id:
            return Response({"error": "upload_id required"}, status=400)

        upload = get_object_or_404(ChunkedUpload, upload_id=upload_id, user=request.user)
        temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp_chunks', upload_id)
        final_dir = os.path.join(settings.MEDIA_ROOT, 'comment_media', str(timezone.now().year), f"{timezone.now().month:02d}", f"{timezone.now().day:02d}")
        os.makedirs(final_dir, exist_ok=True)

        final_file_name = f"{uuid.uuid4()}_{upload.file_name}"
        final_path = os.path.join(final_dir, final_file_name)

        for i in range(upload.total_chunks):
            if not os.path.exists(os.path.join(temp_dir, f'chunk_{i}')):
                return Response({"error": f"Missing chunk {i}"}, status=400)

        with open(final_path, 'wb') as final_file:
            for i in range(upload.total_chunks):
                chunk_path = os.path.join(temp_dir, f'chunk_{i}')
                with open(chunk_path, 'rb') as cf:
                    shutil.copyfileobj(cf, final_file, length=1024*1024)
                os.remove(chunk_path)

        try:
            os.rmdir(temp_dir)
        except:
            pass

        post = None
        parent = None
        if upload.parent_id:
            parent = get_object_or_404(PostComment, id=upload.parent_id)
            post = parent.post
        else:
            post = get_object_or_404(Post, id=upload.post_id)

        comment = PostComment.objects.create(
            post=post, user=request.user, parent=parent, content=upload.content or ""
        )

        relative_path = os.path.relpath(final_path, settings.MEDIA_ROOT)
        dummy = type('obj', (object,), {'name': final_file_name, 'content_type': 'video/mp4'})()
        media_type = get_media_type(dummy)
        if final_file_name.lower().endswith(('.mp4','.mov','.mkv')):
            media_type = 'video'

        CommentMedia.objects.create(
            comment=comment,
            media_type=media_type,
            file=relative_path,
            file_size=upload.total_size,
            mime_type='video/mp4' if media_type=='video' else 'application/octet-stream'
        )

        if parent:
            PostComment.objects.filter(id=parent.id).update(replies_count=F('replies_count') + 1)
        else:
            Post.objects.filter(id=post.id).update(comments_count=F('comments_count') + 1)

        upload.is_completed = True
        upload.save(update_fields=['is_completed'])

        return Response(PostCommentSerializer(comment, context={'request': request}).data, status=201)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({"error": str(e)}, status=500)

# ================= COMMENT REACTION - 5 TYPES =================
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def comment_react(request, comment_id):
    comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
    reaction = request.data.get('reaction', 'like')
    if reaction not in ['like','confuse','wrong','imp','explain']:
        return Response({"error":"Invalid reaction"}, status=400)

    like, created = CommentLike.objects.get_or_create(
        comment=comment, user=request.user,
        defaults={'reaction_type': reaction}
    )
    if not created:
        if like.reaction_type == reaction:
            like.delete()
            my_reaction = None
        else:
            like.reaction_type = reaction
            like.save()
            my_reaction = reaction
    else:
        my_reaction = reaction

    qs = CommentLike.objects.filter(comment=comment).values('reaction_type').annotate(c=Count('id'))
    counts = {r['reaction_type']: r['c'] for r in qs}
    data = {
        'like': counts.get('like',0),
        'confuse': counts.get('confuse',0),
        'wrong': counts.get('wrong',0),
        'imp': counts.get('imp',0),
        'explain': counts.get('explain',0),
        'total': sum(counts.values())
    }
    return Response({"my_reaction": my_reaction, "myReaction": my_reaction, "counts": data, "reaction_counts": data})

# ================= UPDATE - FIXED FOR EDIT =================
class CommentUpdateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(summary="Edit Comment", tags=['Comments'])
    @transaction.atomic
    def patch(self, request, comment_id):
        comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
        if comment.user!= request.user:
            return Response({"error": "Not allowed"}, status=403)

        # FIX 1 - JSON aur FormData dono se content lo
        content = request.data.get('content', None)
        if content is None:
            content = request.data.get('content', '')

        if content is not None and str(content).strip()!= "":
            comment.content = str(content).strip()
            comment.is_edited = True
            comment.save(update_fields=['content', 'is_edited', 'updated_at'])
        elif content is not None and str(content).strip() == "":
            # agar empty bheja to bhi edited mark karo
            comment.is_edited = True
            comment.save(update_fields=['is_edited', 'updated_at'])

        # getlist safe handling
        remove_ids = []
        if hasattr(request.data, 'getlist'):
            remove_ids = request.data.getlist('remove_media_ids')
        if remove_ids:
            CommentMedia.objects.filter(comment=comment, id__in=remove_ids).delete()

        for f in request.FILES.getlist('files')[:5]:
            CommentMedia.objects.create(
                comment=comment,
                media_type=get_media_type(f),
                file=f,
                file_size=f.size,
                mime_type=getattr(f, 'content_type', '')
            )

        return Response(PostCommentSerializer(comment, context={'request': request}).data)

class CommentDeleteAPIView(APIView):
    permission_classes = [IsAuthenticated]
    @transaction.atomic
    def delete(self, request, comment_id):
        comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
        if comment.user!= request.user and not request.user.is_staff:
            return Response({"error": "Not allowed"}, status=403)
        comment.is_deleted = True
        comment.deleted_at = timezone.now()
        comment.save(update_fields=['is_deleted', 'deleted_at'])
        if comment.parent_id:
            PostComment.objects.filter(id=comment.parent_id).update(replies_count=F('replies_count') - 1)
        else:
            Post.objects.filter(id=comment.post_id).update(comments_count=F('comments_count') - 1)
        return Response({"message": "Comment deleted"}, status=200)

class CommentListAPIView(APIView):
    permission_classes = [AllowAny]
    def get(self, request, post_id):
        post = get_object_or_404(Post, id=post_id)
        qs = PostComment.objects.filter(post=post, parent__isnull=True, is_deleted=False).select_related('user').prefetch_related('media')
        if not request.user.is_authenticated or request.user.id!= post.user_id:
            qs = qs.filter(is_hidden=False)
        comments = qs.order_by('-is_pinned', '-created_at')[:50]
        serializer = PostCommentSerializer(comments, many=True, context={'request': request})
        return Response(serializer.data)

class CommentRepliesAPIView(APIView):
    permission_classes = [AllowAny]
    def get(self, request, comment_id):
        parent = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
        # FIX 2 - nested replies ke liye sab reply laayenge
        replies = PostComment.objects.filter(parent=parent, is_deleted=False, is_hidden=False).select_related('user').prefetch_related('media').order_by('created_at')
        serializer = PostCommentSerializer(replies, many=True, context={'request': request})
        return Response(serializer.data)

class CommentHideAPIView(APIView):
    permission_classes = [IsAuthenticated]
    @transaction.atomic
    def post(self, request, comment_id):
        comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
        post = comment.post
        if request.user.id!= post.user_id and not request.user.is_staff:
            return Response({"error": "Only post owner can hide"}, status=403)
        comment.is_hidden = not comment.is_hidden
        if comment.is_hidden:
            comment.hidden_by = request.user
            comment.hidden_at = timezone.now()
            if comment.parent_id is None:
                Post.objects.filter(id=post.id).update(comments_count=F('comments_count') - 1)
        else:
            comment.hidden_by = None
            comment.hidden_at = None
            if comment.parent_id is None:
                Post.objects.filter(id=post.id).update(comments_count=F('comments_count') + 1)
        comment.save(update_fields=['is_hidden', 'hidden_by', 'hidden_at'])
        return Response({"message": "Hidden" if comment.is_hidden else "Unhidden", "is_hidden": comment.is_hidden})
```

### comment_view.py notes
- `CommentDeleteAPIView` decrements `replies_count`/`comments_count`
  directly, but `models.py`'s `update_comments_count` **signal** already
  recomputes `comments_count` from scratch on every `PostComment`
  save/delete (`is_deleted=False` filter). Since soft-delete is a
  `.save()` (not a real delete), the signal fires and recomputes the
  correct count anyway — so the explicit `F('comments_count') - 1` in the
  view is redundant with (but not contradicted by) the signal. No bug,
  just double-computation.
- `CommentHideAPIView`'s manual `comments_count -1/+1` on hide/unhide has
  the **same redundancy** — but note `is_hidden` is **not** part of the
  signal's filter (`is_deleted=False` only), so the view's manual
  adjustment here is actually the **only** thing keeping `comments_count`
  accurate for hidden comments. Don't remove this one without also
  updating the signal.
- `CommentListAPIView`/`CommentRepliesAPIView` are `permission_classes =
  [AllowAny]` — publicly readable without login (unlike almost everything
  else in this app).

---

## 8. `urls.py` (full code)

```python
"""
post/urls.py

⚠️ CRITICAL FIX — the uploaded file did
`from .views import CommentDeleteView, FeedView, PostViewSet, StoryViewSet`
— **none** of these four names exist in `views.py`. `views.py` defines
`PostCreateAPIView`, `HomeFeedView`, `PostListAPIView`, `PostDetailAPIView`,
`PostReactionAPIView`, `PostSaveToggleAPIView`, `SavedPostsListAPIView`,
`serve_media_with_range`; `comment_view.py` defines
`CommentCreateAPIView`, `CommentUpdateAPIView`, `CommentDeleteAPIView`,
`CommentListAPIView`, `CommentRepliesAPIView`, `CommentHideAPIView`, plus
the chunked-upload functions and `comment_react`. This file would raise
`ImportError` on load. Rewritten against the real view names (matching
post_app.md §8), with explicit imports instead of `from .views import *`
/ `from .comment_view import *` for the same reason explicit imports were
used in every other file this session.
"""
from django.conf import settings
from django.urls import path, re_path

from . import comment_view, views
from .comment_view import (
    CommentCreateAPIView,
    CommentDeleteAPIView,
    CommentHideAPIView,
    CommentListAPIView,
    CommentRepliesAPIView,
    CommentUpdateAPIView,
    comment_react,
)
from .views import (
    ExploreFeedAPIView,
    HashtagPostsAPIView,
    HomeFeedView,
    PostCreateAPIView,
    PostDeleteAPIView,
    PostDetailAPIView,
    PostListAPIView,
    PostReactionAPIView,
    PostSaveToggleAPIView,
    SavedPostsListAPIView,
    StoryCreateAPIView,
    StoryListAPIView,
    StoryViewAPIView,
    TrendingHashtagsAPIView,
    serve_media_with_range,
)

urlpatterns = [
    path("create/", PostCreateAPIView.as_view(), name="post-create"),
    path("list/", PostListAPIView.as_view(), name="post-list"),
    path("details/<uuid:id>/", PostDetailAPIView.as_view(), name="post-detail"),
    # NEW — checklist item 57 ("create/list/delete Post") had no delete
    # route anywhere before this; soft-delete, author-or-staff.
    path("<uuid:id>/delete/", PostDeleteAPIView.as_view(), name="post-delete"),
    path("feed/", HomeFeedView.as_view(), name="home-feed"),
    path("like/<uuid:post_id>/reaction/", PostReactionAPIView.as_view(), name="post-reaction"),
    path("<uuid:post_id>/save/", PostSaveToggleAPIView.as_view(), name="post-save-toggle"),
    path("saved/", SavedPostsListAPIView.as_view(), name="saved-posts-list"),

    # NEW — hashtag discovery + explore/discover surface. Overview
    # table lists "Hashtag" and "Explore-content" as core responsibilities
    # of this app; neither had a queryable endpoint before this.
    path("explore/", ExploreFeedAPIView.as_view(), name="post-explore"),
    path("hashtag/<str:tag>/", HashtagPostsAPIView.as_view(), name="hashtag-posts"),
    path("hashtags/trending/", TrendingHashtagsAPIView.as_view(), name="trending-hashtags"),

    # stories — NEW (checklist items 54/55/57/60)
    path("stories/", StoryListAPIView.as_view(), name="story-list"),
    path("stories/create/", StoryCreateAPIView.as_view(), name="story-create"),
    path("stories/<uuid:story_id>/view/", StoryViewAPIView.as_view(), name="story-view"),

    # comments
    path("comment/create/", CommentCreateAPIView.as_view(), name="comment-create"),
    path("comment/<uuid:comment_id>/delete/", CommentDeleteAPIView.as_view(), name="comment-delete"),
    path("comment/post/<uuid:post_id>/", CommentListAPIView.as_view(), name="comment-list"),
    path("comment/<uuid:comment_id>/hide/", CommentHideAPIView.as_view(), name="comment-hide"),
    path("comment/<uuid:comment_id>/replies/", CommentRepliesAPIView.as_view(), name="comment-replies"),
    path("comment/chunked/init/", comment_view.chunked_upload_init, name="chunked-init"),
    path("comment/chunked/chunk/", comment_view.chunked_upload_chunk, name="chunked-chunk"),
    path("comment/chunked/complete/", comment_view.chunked_upload_complete, name="chunked-complete"),
    path("comment/<uuid:comment_id>/react/", comment_react, name="comment-react"),
    path("comment/<uuid:comment_id>/update/", CommentUpdateAPIView.as_view(), name="comment-update"),
]

# ⚠️ Dev-only: `serve_media_with_range` has no auth/permission checks of its
# own (see views.py notes) and re-reads the file from disk on every request
# with no CDN caching in front of it — fine for local development, not
# something to rely on in production. Configure nginx/S3/CDN for real
# media serving there instead (post_app.md §14 issue #6).
if settings.DEBUG:
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", serve_media_with_range, name="serve-media"),
    ]
```

### Full endpoint table (assuming mounted at `/post/`)
| Method | URL | View | Auth | Purpose |
|---|---|---|---|---|
| POST | `/post/create/` | `PostCreateAPIView` | ✅ | Create a post (multipart, up to 10 media files) |
| GET | `/post/list/` | `PostListAPIView` | ✅ | List my posts, or `?target_user_id=` another user's (respects privacy/follow) |
| GET | `/post/details/<uuid:id>/` | `PostDetailAPIView` | ✅ | Single post + first 10 top-level comments + view tracking |
| DELETE | `/post/<uuid:id>/delete/` | `PostDeleteAPIView` | ✅ | Soft-delete own post (or staff) — see §16.4 |
| GET | `/post/feed/` | `HomeFeedView` | ✅ | Home feed (following-first, trending fallback) |
| POST/GET | `/post/like/<uuid:post_id>/reaction/` | `PostReactionAPIView` | POST ✅ / GET public | Toggle a 5-type reaction / get counts |
| POST | `/post/<uuid:post_id>/save/` | `PostSaveToggleAPIView` | ✅ | Save/unsave toggle |
| GET | `/post/saved/` | `SavedPostsListAPIView` | ✅ | My saved posts, optional `?collection_name=` |
| GET | `/post/explore/` | `ExploreFeedAPIView` | ✅ | Explore/discover feed — public posts excluding own + already-followed, engagement-ranked (see §18.2) |
| GET | `/post/hashtag/<str:tag>/` | `HashtagPostsAPIView` | ✅ | Public posts containing `#tag` (case-insensitive, see §18.1/§18.2) |
| GET | `/post/hashtags/trending/` | `TrendingHashtagsAPIView` | ✅ | Trending hashtags, `?days=7&limit=20` (see §18.2) |
| GET | `/post/stories/` | `StoryListAPIView` | ✅ | Non-expired stories (see §16.2) |
| POST | `/post/stories/create/` | `StoryCreateAPIView` | ✅ | Create a story (image/video, expires per §16.2) |
| POST | `/post/stories/<uuid:story_id>/view/` | `StoryViewAPIView` | ✅ | Record a story view (dedup per viewer) |
| POST | `/post/comment/create/` | `CommentCreateAPIView` | ✅ | Create comment/reply (small files, direct upload) |
| DELETE | `/post/comment/<uuid:comment_id>/delete/` | `CommentDeleteAPIView` | ✅ | Soft-delete own comment (or staff) |
| GET | `/post/comment/post/<uuid:post_id>/` | `CommentListAPIView` | public | Top-level comments for a post |
| POST | `/post/comment/<uuid:comment_id>/hide/` | `CommentHideAPIView` | ✅ | Post-owner hides/unhides a comment |
| GET | `/post/comment/<uuid:comment_id>/replies/` | `CommentRepliesAPIView` | public | Replies under a comment |
| POST | `/post/comment/chunked/init/` | `chunked_upload_init` | ✅ | Start a large (up to 4GB) video-comment upload |
| POST | `/post/comment/chunked/chunk/` | `chunked_upload_chunk` | ✅ | Upload one chunk |
| POST | `/post/comment/chunked/complete/` | `chunked_upload_complete` | ✅ | Assemble chunks → finalize comment |
| POST | `/post/comment/<uuid:comment_id>/react/` | `comment_react` | ✅ | Toggle a 5-type reaction on a comment |
| PATCH | `/post/comment/<uuid:comment_id>/update/` | `CommentUpdateAPIView` | ✅ | Edit content / add / remove comment media |
| GET | `/media/<path>` | `serve_media_with_range` | public | Range-enabled media streaming (DEBUG mode only) |

### Notes
- `CommentUpdateAPIView` route (`comment/<uuid:comment_id>/update/`) is
  live and working; the file had a leftover **commented-out** duplicate
  route (`comment/<uuid:comment_id>/edit/` → `CommentUpdateAPIView`,
  disabled) — only one active route exists, no conflict.
- ⚠️ `serve_media_with_range` is only wired up when `DEBUG=True`. In
  production you must serve `MEDIA_URL` some other way (nginx `X-Accel-
  Redirect`, S3 signed URLs, a CDN, etc.) — Range-request video/audio
  streaming will **not work** through this app's own URL config in
  production as currently written.
- ✅ **Resolved:** the earlier `from .views import *` / `from .comment_view
  import *` wildcard imports (which pulled in four names —
  `CommentDeleteView`, `FeedView`, `PostViewSet`, `StoryViewSet` — that
  don't exist anywhere in `views.py`/`comment_view.py`, an `ImportError`
  on load) are gone. The active file above uses explicit named imports
  against the real view classes, and the unused `from django.conf.urls.
  static import static` import is gone too.

---

## 9. `admin.py` (full code)

```python
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
```

Note: `ChunkedUpload` and `CommentLike` (both defined in `models.py`) are
**not** registered in admin — you won't see them in `/admin/` unless you
add `admin.site.register(ChunkedUpload)` / `admin.site.register(CommentLike)`
yourself. Not a bug, just something to be aware of if you need to inspect
those tables via admin.

---

## 10. `apps.py`

```python
from django.apps import AppConfig


class PostConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "post"

    def ready(self):
        import post.signals  # noqa: F401  — registers the post_save/post_delete receivers
```

⚠️ Updated from the doc's original (no `ready()`): `signals.py` now owns
`posts_count` bookkeeping (§16), so its receivers need to actually be
imported once at startup — Django doesn't auto-discover `signals.py` the
way it auto-discovers `models.py`. Filename must be lowercase
`signals.py` — `import post.signals` will not resolve a `Signals.py` on
a case-sensitive filesystem (Linux/prod).

---

## 11. `tests.py`

No longer empty — see §17.5 (and §18.3 for the two hashtag/explore
classes added after §17.5 was written) for what's covered — this is the
current, accurate list:
`PostCreateTests`, `PostDeleteTests`, `ReactionIdempotencyTests`,
`CommentThreadingTests`, `SavePostTests`, `HashtagDiscoveryTests`,
`ExploreFeedTests`, `StoryExpiryTests`.

---

## 12. `__init__.py`

Empty — standard Python package marker, nothing to configure.

---

## 13. Business Logic Flows

### 13.1 Create Post (`POST /post/create/`)
```
Validate: text posts need content; image/video/document posts need media_files
        │
        ▼
Auto-generate hashtags from #tags in content (if not explicitly provided)
Auto-generate unique slug from title/content/uuid
        │
        ▼
Create Post row, then create one PostMedia row per uploaded file (display_order = index)
        │
        ▼
User.posts_count += 1 (F() update)
        │
        ▼
201 response with full post + media (via to_representation)
```
If any uploaded media is a video, `auto_generate_video_thumbnail` (§3
signal) fires automatically in the background of the same request (via
`post_save` on `PostMedia`) — extracts a frame at 1 second via ffmpeg.

### 13.2 Home Feed algorithm (`GET /post/feed/`)
```
Get IDs of accounts I follow (ACCEPTED only)
        │
   any following?
   │              │
  yes              no
   │                │
   ▼                ▼
Posts from people   Posts from EVERYONE (public only),
I follow, last 7     last 7 days, ranked by:
days prioritized,     likes×3 + comments×5 + shares×10
ranked by:            → "trending" fallback feed
is_recent(1st),
likes×3+comments×5+shares×10+views×0.1
   │
   any results?
   │           │
  yes           no
   │             │
   ▼             ▼
return them   fall through to trending-everyone feed (same as "no" branch above)
```
Always excludes: your own posts, soft-deleted, non-approved moderation
status, and `is_sensitive=True` posts.

### 13.3 Post Detail + View Tracking (`GET /post/details/<id>/`)
```
is_deleted?           → 404
visibility=private &  → 403 (unless you're the owner)
  not owner?
visibility=connections → 403 unless you follow the owner (ACCEPTED) or are the owner
& not following/owner?
        │
        ▼
PostView.get_or_create(post, user)  — one view record per (post, user) pair,
                                       so repeat visits don't duplicate rows
Post.views_count += 1               — but the COUNT increments every single
                                       time regardless (not deduped to the
                                       get_or_create) — see §14 note
        │
        ▼
200 with post + media + first 10 top-level, non-hidden, non-deleted comments
```

### 13.4 Reaction toggle — Post & Comment (same pattern, §6 `PostReactionAPIView.post` / §7 `comment_react`)
```
No existing reaction from me on this post/comment
        → create it (status: "liked")
Existing reaction, SAME type as requested
        → delete it (status: "unliked"/None) — un-reacting
Existing reaction, DIFFERENT type than requested
        → update reaction_type in place (status: "changed")
```
Signals (§3) recompute `Post.like_count`/`confuse_count`/etc. (or
`PostComment.likes_count` for comments) after every create/update/delete.
5+ `wrong` reactions on a post auto-sets `moderation_status='flagged'`.

### 13.5 Save / Unsave toggle (`POST /post/<id>/save/`)
Simple existence-check toggle: exists → delete (unsave); doesn't exist →
create (save, with optional `collection_name`, default `'default'`).
`saves_count` maintained by the `update_saves_count` signal.

### 13.6 Comment creation — two paths
**Regular** (`POST /post/comment/create/`, ≤ small files, direct
multipart upload):
```
Resolve parent (reply) or post (top-level) from post_id/parent_id
Nesting depth: UNLIMITED (old 1-level restriction removed)
is_comments_disabled on the post? → 403
content empty AND no files? → 400
        │
        ▼
Create PostComment, then up to 5 CommentMedia rows
Update parent.replies_count OR post.comments_count (+1)
```

**Chunked** (for files up to 4GB — e.g. long video comments):
```
POST .../chunked/init/     → validate size ≤4GB, create ChunkedUpload row,
                              make a temp_chunks/<upload_id>/ directory
        │
        ▼ (repeat per chunk, client-driven)
POST .../chunked/chunk/    → write chunk_<index> file into temp dir,
                              return progress %
        │
        ▼ (after all chunks sent)
POST .../chunked/complete/ → verify all chunk files present
                              concatenate them into final_path under
                              MEDIA_ROOT/comment_media/YYYY/MM/DD/
                              delete temp chunk files + temp dir
                              create PostComment + CommentMedia
                              (media_type forced to 'video' if the
                              filename ends in .mp4/.mov/.mkv)
                              mark ChunkedUpload.is_completed = True
```

### 13.7 Comment edit / delete / hide
- **Edit** (`PATCH .../update/`): only the comment's own author; can
  change `content` (marks `is_edited=True` even if cleared to empty) and
  add up to 5 new files / remove existing media by ID list
  (`remove_media_ids`).
- **Delete** (`DELETE .../delete/`): soft delete only (`is_deleted=True`,
  `deleted_at` set) — author or staff. Row is never actually removed from
  the DB.
- **Hide** (`POST .../hide/`): only the **post owner** (or staff) can
  hide/unhide someone else's comment on their post — a lightweight
  per-post moderation tool, independent of delete.

### 13.8 Media serving with Range support (`serve_media_with_range`, DEBUG only)
Supports HTTP `Range` header for `video/*`/`audio/*` content types →
206 Partial Content responses (needed for browser/mobile video seeking).
Path-traversal guarded (`'..' in path` / leading `/` rejected). Sets
`Content-Disposition: inline` for PDFs/images/video/audio (viewable in
browser) vs. `attachment` for office docs/archives (forced download).

---

## 14. Known Issues / Things To Double-Check

1. **`Post.views_count` isn't deduped even though `PostView` is**
   (§13.3) — `PostView.objects.get_or_create(post=instance, user=request.user)`
   only creates one *view record* per user, but the very next line
   (`Post.objects.filter(id=instance.id).update(views_count=F('views_count') + 1)`)
   increments the counter **unconditionally on every request**, including
   repeat visits by the same user. If you want "unique viewers" semantics
   for the count (not just the log table), only increment when
   `get_or_create`'s `created` flag is `True`.
2. ✅ **RESOLVED** — the two different, same-named `PostCommentSerializer`
   classes (one in `serializers.py`, simple; one in
   `comment_serializers.py`, full, with media/reactions) no longer share
   a name. The `serializers.py` one is renamed to
   `PostCommentPreviewSerializer` — see §4.
3. **Redundant `comments_count`/`replies_count` bookkeeping** —
   `CommentDeleteAPIView` (and `update_comments_count` signal) both adjust
   the count on delete; harmless but doubled work. `CommentHideAPIView`'s
   manual adjustment, however, **is load-bearing** (the signal doesn't
   know about `is_hidden`) — don't remove it without updating the signal
   too. See §7 note.
4. **Two signal handlers on `PostLike`** (`update_likes_count` and
   `update_reaction_counts`) both recompute `Post.likes_count` on every
   save/delete — redundant, could be merged into one handler.
5. **`auto_generate_video_thumbnail` requires local filesystem storage**
   (`instance.file.path`) and the **`ffmpeg` binary installed on the
   host** — will silently fail (caught exception, just prints) on cloud
   storage backends (S3/GCS) or if ffmpeg isn't installed. Since it only
   `print()`s errors (not `logger`), these failures won't show up in
   normal Django logging unless you're watching stdout.
6. **`serve_media_with_range` only active when `DEBUG=True`** — you must
   set up real media serving (nginx, S3, CDN) for production; Range-
   request video seeking won't work through this app's own routing in
   prod as-is. See §8 note.
7. **`ChunkedUpload.post_id`/`parent_id` are plain `CharField`, not FKs**
   — no DB-level referential integrity; a stale/invalid `post_id` passed
   to `chunked_upload_complete` will only fail at `get_object_or_404`
   time, not earlier.
8. ✅ **RESOLVED** — the unused dead-code `PostSerializer` in
   `serializers.py` has been removed.
9. **`ReactionRequestSerializer` is defined twice** — once in
   `serializers.py`, once locally inside `views.py`. No functional
   collision (explicit imports), but worth consolidating to one
   definition.
10. **`is_comments_disabled` is only enforced in `CommentCreateAPIView`**
    (regular upload path) — the **chunked upload path**
    (`chunked_upload_init`/`_complete`) does **not** check
    `post.is_comments_disabled` before accepting a video comment. If this
    flag matters to you, add the same check to the chunked flow.

---

## 15. Quick Setup Checklist (to run this app standalone)

- [ ] `login` app installed & migrated (`AUTH_USER_MODEL` set, with
      `posts_count`, `is_private`, `profile_photo` fields — see that
      app's own reference doc).
- [ ] `user_profile` app installed & migrated (`Follow` model — see that
      app's own reference doc); `post/views.py` imports it directly.
- [ ] `'rest_framework'`, `'drf_spectacular'`, `'post'` in `INSTALLED_APPS`.
- [ ] `pip install djangorestframework drf-spectacular ffmpeg-python`.
- [ ] **`ffmpeg` binary** installed on the OS/server (video thumbnail
      generation shells out to it).
- [ ] `MEDIA_URL` / `MEDIA_ROOT` configured; local filesystem storage
      (not S3/cloud) if you want video auto-thumbnails to keep working.
- [ ] `path('post/', include('post.urls'))` (or your chosen prefix) in
      root `urls.py`.
- [ ] Run `python manage.py makemigrations post && python manage.py migrate`.
- [ ] For production media serving (Range/video streaming outside
      `DEBUG`): configure nginx/S3/CDN separately — this app's own
      `serve_media_with_range` only activates when `DEBUG=True`.

With the above satisfied, everything in this single document — models,
serializers, comment_serializers, views, comment_view, urls, admin — is
enough to run the full `post` app end to end.

---

## 16. Addendum — Story feature, services/signals/tasks, posts_count, post-delete

This section documents the gap between the checklist (items 54–64) and
what §1–§15 above described as "the app", and the decisions made to
close it.

### 16.1 What was actually missing

Four files existed outside what §1's table originally listed
(`services.py`/`Services.py`, `signals.py`/`Signals.py`,
`tasks.py`/`Tasks.py`, and a much larger `tests.py`), but all four
referenced models that don't exist in `models.py` as documented in §3:
`Hashtag`, `PostHashtag`, `Like`, `SavedPost`, `Comment`, `Story`. Real
model names are `PostLike`, `PostSave`, `PostComment` — and `Story` /
`StoryView` genuinely didn't exist anywhere at all, despite checklist
items 54/55/57/60 asking for them.

Separately, all three of `Services.py` / `Signals.py` / `Tasks.py` were
capitalized. `apps.py` and `serializers.py` import them in lowercase
(`import post.signals`, `from .services import ...`) — on a
case-sensitive filesystem (any real Linux server) that import fails
outright. **Filenames must be `services.py` / `signals.py` / `tasks.py`.**

### 16.2 Story / StoryView — now real (checklist items 54/55/57/60)

Added to `models.py`, following the same conventions as every other model
in this app (UUID pk, soft-delete via `is_deleted`/`deleted_at`,
`-created_at` ordering):

```python
class Story(models.Model):
    MEDIA_TYPE_CHOICES = [('image', 'Image'), ('video', 'Video')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='stories')
    media = models.FileField(
        upload_to='stories/%Y/%m/%d/',
        validators=[FileExtensionValidator(allowed_extensions=['jpg','jpeg','png','gif','mp4','mov'])],
    )
    media_type = models.CharField(max_length=10, choices=MEDIA_TYPE_CHOICES, default='image')
    caption = models.CharField(max_length=300, blank=True)
    views_count = models.PositiveIntegerField(default=0)          # kept in sync by a signal, like saves_count/shares_count
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(default=default_story_expiry, db_index=True)  # now() + 24h at creation time

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at'])


class StoryView(models.Model):
    story = models.ForeignKey(Story, on_delete=models.CASCADE, related_name='views')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='story_views')
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['story', 'user']   # one view credit per (story, viewer)
```

`Story.views_count` is kept in sync by an `update_story_views_count`
signal (`post_save`/`post_delete` on `StoryView`) — same pattern as
`update_saves_count`/`update_shares_count` already in §3.

**New endpoints** (`urls.py`):

| Method | Path | View | Notes |
|---|---|---|---|
| POST | `/post/stories/create/` | `StoryCreateAPIView` | multipart, `media` (+ optional `caption`) |
| GET  | `/post/stories/` | `StoryListAPIView` | own stories + followed users', `expires_at > now`, `is_deleted=False` — filtered in real time, same as the feed |
| POST | `/post/stories/<id>/view/` | `StoryViewAPIView` | dedup’d per viewer via `unique_together`; 404 if expired |

**`tasks.py`** — `expire_old_stories()` (soft-deletes rows past
`expires_at`) and `hard_delete_ancient_stories(days=30)` (purges
soft-deleted rows older than `days`) now run against the real `Story`
model. Register both in `CELERY_BEAT_SCHEDULE`:

```python
CELERY_BEAT_SCHEDULE = {
    "expire-old-stories": {"task": "post.tasks.expire_old_stories", "schedule": crontab(minute="*/15")},
    "purge-ancient-stories": {"task": "post.tasks.hard_delete_ancient_stories", "schedule": crontab(hour=3, minute=0, day_of_week=0)},
}
```

Note the task is pure housekeeping, not a visibility gate —
`StoryListAPIView` already filters `expires_at__gt=now()` live, so an
expired-but-not-yet-soft-deleted story is already invisible before the
task ever runs.

### 16.3 `posts_count` — moved to a signal (checklist item 58)

`PostCreateAPIView.post()` used to increment `posts_count` manually right
after `serializer.save()`. That line is now **removed**; `signals.py`'s
`increment_posts_count_on_create` (`post_save`, `created=True`) is the
single source of truth instead — matching item 58's original wording.
Rationale: no future code path that creates a `Post` (admin, a
management command, a data migration, a test using
`Post.objects.create()` directly) can silently forget to bump the
counter, because it was never that path's job to remember in the first
place.

Decrementing mirrors this split deliberately:
- **Hard delete** — `decrement_posts_count_on_hard_delete`, a real
  `post_delete` signal (registered, currently inert — nothing in this
  app hard-deletes a `Post`).
- **Soft delete** — `decrement_posts_count_on_soft_delete(post)`, a
  plain function, called explicitly from `PostDeleteAPIView.delete()`
  (soft-delete is just a `.save()`, it never fires `post_delete`). Same
  split `CommentDeleteAPIView` already uses implicitly for
  `comments_count`.

### 16.4 Post delete endpoint — now exists (checklist item 57)

`§13`'s flows never included a delete-Post path, and no such route
existed in `urls.py` despite item 57 asking for "create/list/delete
Post". Added:

```
DELETE /post/<id>/delete/   → PostDeleteAPIView
```

Soft delete only (`is_deleted=True`, `deleted_at` set) — author or
staff, otherwise 403. Calls `decrement_posts_count_on_soft_delete` (see
16.3). Mirrors `CommentDeleteAPIView`'s pattern in `comment_view.py`
exactly.

### 16.5 `services.py` — what's real, what got cut (checklist items 61/63)

- **Cut entirely:** `attach_hashtags()` and the Hashtag/PostHashtag idea.
  `Post.hashtags` is a plain `JSONField`, populated directly inside
  `PostCreateSerializer.create()` (§4/§13.1) — there was never a
  second model to attach anything to. The uploaded `serializers.py` had
  a leftover dead call into this (`from .services import
  attach_hashtags`) that has been deleted from
  `PostCreateSerializer.create()`.
- **Cut entirely:** `toggle_like()` / `toggle_save()` / `add_comment()` /
  `delete_comment()`. These duplicated logic already implemented
  correctly in `views.py`/`comment_view.py` against the real models,
  with counters kept in sync by the signals already in `models.py`
  (§3). Nothing in this app called these functions; they were dead code
  against nonexistent models, not a missing feature.
- **Kept, fixed field names, still unwired:**
  - `notify_post_liked(post, actor)` / `notify_post_commented(post, comment)`
    — the `core.create_notification` soft-dependency probe from checklist
    item 63 / Phase 3's hub. Call the first from
    `PostReactionAPIView.post()` (only the `status_msg == "liked"`
    branch) and the second from `CommentCreateAPIView.post()` (only for
    new top-level comments) once `core.notifications` ships.
  - `share_post_to_conversation(post, sender, conversation_id)` —
    checklist item 61. Fixed to use `post.user`/`post.content` and
    `PostShare.objects.get_or_create` (the real model has
    `unique_together = ['post', 'user']`). No `/share/` route exists yet
    — add `POST /post/<id>/share/` once the `message` app's real
    send-function path is confirmed.

### 16.6 Known-issue list (§14) — status update

Issue **#8** ("`PostSerializer` in `serializers.py` appears unused") and
**#9** (duplicate `ReactionRequestSerializer`) are unchanged, still open.
All other numbered issues in §14 are unaffected by this addendum.


---

## 17. Addendum 2 — services/signals/tasks/admin/tests reconciled against separately-uploaded capitalized versions

§16 described the first real versions of these five files. Afterwards,
a *second* set was uploaded — `Services.py`, `Signals.py`, `Tasks.py`,
`admin.py`, `tests.py` (capitalized on the first three, matching the
exact case-sensitivity trap §16.1 already flagged). This section
documents what was kept, what was fixed, and — for `services.py`
specifically — a real bug that neither §16's version nor the newly
uploaded one had caught.

### 17.1 `services.py` — wrong core module/signature, now fixed

The newly-uploaded `Services.py` called:

```python
from core.notifications import create_notification
create_notification(recipient=..., actor=..., verb=..., target_type=..., target_id=..., payload=...)
```

Checked against `core_app_documentation.md` §4/§5, **neither the module
path nor the signature match**:

```python
# core.services — single discrete event, no actor/target_id
create_notification(recipient, notif_type, title, message="", *, classroom=None, session=None, data=None)

# core.notification_batching — burst events, THIS is where actor/target_id live
create_batched_notification(*, recipient, notif_type, actor, target_id,
    title_fn, message_fn=None, classroom=None, session=None,
    extra_data=None, window_seconds=120, send_push_fn=None)
```

`core_app_documentation.md` §5 gives "5 people liked your post in 10
seconds shouldn't become 5 separate notifications" as its own motivating
example for `create_batched_notification` — a like is exactly that
burst case. So the final `services.py`:

- `notify_post_liked(post, actor)` → **`core.notification_batching.create_batched_notification`**
  (with a `title_fn` that reads "X liked your post" for one actor,
  "X and N others liked your post" once a window has more than one).
- `notify_post_commented(post, comment)` → **`core.services.create_notification`**
  (a single discrete event, same as every other real call-site shown in
  core's own doc).

Both remain soft dependencies (`try/except ImportError`, log + no-op) —
`core.notifications`/`core.notification_batching` don't exist yet, and
this app must still import cleanly without them. Still **unwired** from
`PostReactionAPIView`/`CommentCreateAPIView` for the same reason as
§16.5: the real `NotifType` enum (see core_app_documentation.md) has no
`POST_LIKED`/`POST_COMMENTED` value yet — `POST_LIKED_NOTIF_TYPE`/
`POST_COMMENTED_NOTIF_TYPE` in `services.py` are placeholders to add to
that enum first.

`share_post_to_conversation()` — functionally unchanged from §16.5
(`post.user`/`post.content`, `PostShare.objects.get_or_create`). The
exact `message` app send-function signature still isn't visible from
anything uploaded so far, so the `message.services.send_message(...)`
call is still a best-guess — adjust once that file is available. Made
it record the `PostShare` row *before* attempting chat delivery, so a
share is captured even if `message` isn't installed or delivery fails.

### 17.2 `signals.py` — added a defensive guard

Functionally identical to §16.3's version, plus one addition from the
newly-uploaded `Signals.py`: every function now checks
`hasattr(User, "posts_count")` before touching it, logging a warning and
skipping instead of raising `AttributeError` if `login.User` ever loses
that field. `posts_count` lives on an app outside this one — post
create/delete shouldn't hard-crash over a bookkeeping field on someone
else's model. `decrement_posts_count_on_soft_delete(post)`'s signature
is unchanged, so `views.py`'s import of it still resolves.

### 17.3 `tasks.py` — return values fixed for testability + a cascade-count bug

Two real bugs, both now fixed:

1. The version circulating after §16 returned a human-readable string
   (`"expired 1 stories"`). `tests.py` (§17.5) asserts
   `expire_old_stories() == 1` — an `int`. Both tasks now return the
   plain row count via `QuerySet.update()`/`.delete()`'s own return
   value directly, no `.count()` pre-check and no per-instance
   `.soft_delete()` loop needed.
2. `QuerySet.delete()`'s first return value is the **total** rows
   deleted across every cascaded model — e.g. each purged `Story`'s
   `StoryView` rows via `on_delete=CASCADE` — not just `Story` rows.
   `hard_delete_ancient_stories()` now pulls the `Story`-specific count
   out of the per-model breakdown dict (`per_model["post.Story"]`)
   instead of returning the inflated total.

### 17.4 `admin.py` — upgraded from bare registration to real ModelAdmins

§16 (and the doc's original §9) only had `admin.site.register(Model)`
calls. The newly-uploaded `admin.py` replaced these with real
`ModelAdmin` subclasses — `list_display`, `list_filter`,
`search_fields`, `autocomplete_fields`, a `PostMediaInline` on
`PostAdmin`, `readonly_fields` on the denormalized counters. Every field
referenced was checked against the real `models.py` (e.g. `is_hidden`,
`hidden_by`, `collection_name`, `upload_id`, `views_count`,
`expires_at`) — all genuinely exist. Adopted as-is. Registers all
twelve real models, including `ChunkedUpload`/`CommentLike` (§9 had
explicitly left these two out) and `Story`/`StoryView` (§1's file table
promised this, §9's code block never delivered it).

### 17.5 `tests.py` — merged two drafts, found one more real bug

Two independent `tests.py` drafts existed by this point — one from §16,
one newly uploaded. Merged into a single file, using `reverse()` with
the `name=` values from `urls.py` (§8) rather than hardcoded paths, so
the tests don't stop testing anything if the root `urls.py` ever mounts
this app under a different prefix than `post/`.

**Bug found while merging:** `PostSaveToggleAPIView` (§6) is a plain
create-or-delete, not a single idempotent status — it returns **201** on
save and **200** on unsave. The §16-era draft asserted `200` on both
branches, which would have failed against the real view. Fixed.

Final test classes, one file, no duplication:

| Class | Covers |
|---|---|
| `PostCreateTests` | create, empty-content-on-text-post rejection, hashtag auto-extraction, `posts_count` incremented exactly once |
| `PostDeleteTests` | author/staff/stranger permissions, full create→delete cycle nets back to the original `posts_count` |
| `ReactionIdempotencyTests` | like→unlike→relike, reaction-type change doesn't duplicate the row, counter always matches actual row count |
| `CommentThreadingTests` | top-level vs reply counters, soft-delete decrements, replies endpoint scoping, comments-disabled 403 |
| `SavePostTests` | save/unsave status codes (201/200) + counts, saved-list scoped to the requesting user |
| `HashtagDiscoveryTests` | ⚠️ **added after this table, in §18.3** — normalization on create, lookup finds posts regardless of original casing, private/deleted posts excluded, trending counts sum correctly across authors |
| `ExploreFeedTests` | ⚠️ **added after this table, in §18.3** — excludes own + followed posts, excludes private posts, category filter |
| `StoryExpiryTests` | `is_expired`, `expire_old_stories`/`hard_delete_ancient_stories` return counts, expired stories excluded from listing, view-count dedup per viewer |

Not yet verifiable end-to-end: `login.User` (does it really carry
`posts_count`, checked via `hasattr` in §17.2?) and `user_profile.Follow`
weren't uploaded as real files at any point in this thread — everything
above is confirmed by static cross-reference (imports resolve, field
names exist on the real models, return types match what callers expect)
but has not been run against a live Django test database.


---

## 18. Addendum 3 — Hashtag discovery + Explore-content were missing entirely

A broader app-responsibility list (outside anything uploaded in this
thread) named this app's scope as: **Post, Comment, Like, Story,
Hashtag, Feed, Explore-content**. Checked each against the actual
routes in `urls.py` (§8) — five of seven had real endpoints; two
didn't:

- **Hashtag** — `Post.hashtags` (§3) was write-only. It got populated
  on create and just sat on the row; nothing anywhere read it back out.
  No way to browse "posts tagged #x" or see what's trending.
- **Explore-content** — `HomeFeedView` (§6) has a public-post fallback
  branch for when the requester follows nobody / their follows haven't
  posted recently, but that only ever surfaces *inside* the following
  feed as a fallback. There was no standalone discovery surface — the
  "Explore tab" pattern — a user could open any time regardless of who
  they follow.

### 18.1 Bug found while fixing Hashtag: casing was never normalized

`PostCreateSerializer.create()` (§4) saved hashtags exactly as typed —
`"#Django"` and `"#django"` landed as two different strings in the
JSONField. Harmless as long as nothing read the field back, which
nothing did until now. Fixed at the source: both auto-extracted
(`#\w+` regex matches) and client-supplied `hashtags` are now
lowercased + deduped before the `Post` row is created, so every future
consumer of `Post.hashtags` gets consistent values without needing to
normalize on the read side.

### 18.2 New endpoints

```
GET  /post/hashtag/<tag>/         → HashtagPostsAPIView
GET  /post/hashtags/trending/     → TrendingHashtagsAPIView  (?days=7&limit=20)
GET  /post/explore/               → ExploreFeedAPIView       (?category=...)
```

- `HashtagPostsAPIView` — public posts containing the given tag
  (case-insensitive by construction, since §18.1 normalizes at write
  time), ranked by the same engagement-score formula `HomeFeedView`
  already uses. Uses JSONField `__contains` — native on Postgres,
  needs SQLite's JSON1 extension (Django ≥3.1, standard on modern
  Python builds) if that's the target DB.
- `TrendingHashtagsAPIView` — Python-level `Counter` over the most
  recent 2000 public posts in the lookback window, not a real SQL
  aggregation (JSONField list elements aren't portably GROUP-BY-able
  across Postgres/SQLite). Same call as checklist item 60 made for
  feed fan-out: fine at current scale, swap for a Redis sorted set
  incremented at post-create time once volume actually demands it.
- `ExploreFeedAPIView` — public posts, excluding the requester's own
  AND already-followed accounts (so it stays genuinely about finding
  new accounts rather than duplicating the home feed), engagement-
  ranked over a 30-day window with the same all-time fallback pattern
  `HomeFeedView` uses when the recent window is empty.

### 18.3 Tests added

`HashtagDiscoveryTests` (normalization on create, lookup finds posts
regardless of original casing, private/deleted posts excluded, trending
counts sum correctly across different authors) and `ExploreFeedTests`
(excludes own + followed posts, excludes private posts, category
filter) — both in `tests.py` (§17.5), following the same `reverse()`-
based pattern as the rest of that file.