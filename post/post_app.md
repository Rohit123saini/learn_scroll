# `post` App — Complete Self-Contained Reference

Ye ek hi file hai jisme poore **post** (feed, likes, comments, saves, media)
Django app ka sara logic, code, connections, flows aur known issues cover
hain. Iske alawa kisi aur file ki zaroorat nahi — sab kuch (models →
serializers → comment_serializers → views → comment_view → services →
signals → tasks → urls → admin → apps.py) yahin milega, saath me har piece
kya kaam karta hai uski explanation bhi.

> **Latest pass — Addendum 6 (§21):** full **byte-for-byte verification**
> of every code section in this doc against the actual uploaded source
> (Sep 2026 sync pass, same treatment as `login_app_reference.md`). One
> real bug found and fixed: §5 (`comment_serializers.py`) had an old,
> dead, commented-out draft accidentally pasted *ahead of* the real code
> inside the same code fence — the note above it claimed "only the
> active code is included" but the block itself contradicted that. Fixed
> — §5 now contains exactly and only the current active file. Also
> **added full "full code" sections for `services.py`, `signals.py`, and
> `tasks.py`** (§10.1–§10.3) — these three existed only as scattered
> excerpts across earlier addenda before now; they have one canonical,
> current, verified home like every other file in this app. Everything
> else (`models.py`, `serializers.py`, `views.py`, `comment_view.py`,
> `urls.py`, `admin.py`, `apps.py`) was diffed line-by-line and already
> matched exactly — no changes needed there. See §21 for the full
> changelog of this pass.
>
> **Previous pass:** §20 (Addendum 5) — the `PostLike` duplicate-signal
> issue tracked since §19.2 (B-5) is now resolved, a new restrict-aware
> comment-preview feature (G-3) was added, and two stale doc sections
> (`urls.py` §8, `admin.py` §9's trailing note) were synced back up with
> code that had already changed earlier.

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

**Tech stack:** Django + DRF + `drf-spectacular` (OpenAPI docs) + Celery
(async video-thumbnail generation, Story expiry — see §19 Addendum 4) +
the `ffmpeg` CLI via `subprocess` (**not** the `ffmpeg-python` pip package
— see §19.4) + Django signals (denormalized counters).

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
| `services.py` | Notification hookup (post-liked/commented, wired in) + share-to-conversation helper. See §19.1 (supersedes §16.5/§17.1). |
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
    'user_profile',   # provides the Follow model used by this app, and
                       # (as of G-3) RestrictUser — see §4
    'post',
]

AUTH_USER_MODEL = "login.User"   # must have: posts_count, is_private, profile_photo (see login app doc)

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
```

Packages needed (pip):
```
djangorestframework drf-spectacular celery
```
Plus the **`ffmpeg` binary itself** must be installed on the server/OS —
`post/services.py`/`post/tasks.py` shell out to it directly via
`subprocess` (see §19.4); there is **no `ffmpeg-python` pip dependency**
(TASK 27 removed it — the older draft of `models.py` used to import it,
see §3's model notes). Celery + a broker (Redis, etc.) must be running
for video-thumbnail generation (§19.4) and the Story-expiry tasks (§16.2)
to actually execute — without a worker, `generate_video_thumbnail.delay()`
/ the beat schedule just enqueue and nothing consumes them.

Two more settings this app now reads, both optional (safe defaults if
unset — see §19.3):
```python
SERVE_MEDIA_VIA_DJANGO = False   # gates the /media/ Range-serving fallback route — see §8, §19.3
USE_S3_STORAGE = False           # if True together with SERVE_MEDIA_VIA_DJANGO=True, settings.py should raise ImproperlyConfigured at startup — see §19.3
```

Root `urls.py`:
```python
path('post/', include('post.urls')),   # or your chosen prefix — see §9
```

⚠️ `views.py` imports `from user_profile.models import Follow` directly,
and (as of G-3) `serializers.py`'s `PostDetailSerializer.get_comments()`
does a lazy `from user_profile.models import RestrictUser` inside the
method — this app **cannot run** unless the `user_profile` app (see its
own reference doc) is installed and migrated first.

⚠️ **(TASK 3, new)** `tasks.py`'s `notify_followers_new_post` also does
top-of-function (not try/except-guarded) lazy imports of
`user_profile.models.Follow`/`RestrictUser` and
`core.models.Notification`/`core.services.create_bulk_notifications` —
same "`user_profile` is required, not optional" reasoning `services.py`
already applies to `core` for the like/comment notification path. See
§10.3 and §22.

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
```

### Model notes
- **`update_likes_count` was removed** (see the `NOTE (fix...)` comment in
  the code above, still accurate) — it duplicated `update_reaction_counts`,
  which already recomputed `likes_count` as the sum of all per-reaction-type
  counts. `update_reaction_counts` was kept as the superset — at the time.
- ✅ **RESOLVED (B-5) — the duplicate-receiver issue this doc previously
  flagged as "NEW ISSUE (not yet fixed)" is now fixed.** `models.py`'s own
  `update_reaction_counts` — the second `@receiver` on `PostLike`'s
  `post_save`/`post_delete` that ran alongside `signals.py`'s
  `sync_post_reaction_counts_on_save`/`_on_delete` — has been **deleted
  from this file**, along with its now-unused `from django.db.models
  import Count` import. `signals.py`'s version is the one that survived
  (see the `NOTE (fix, see B-5)` comment in the code above, and §19.2
  which now records this as resolved). Every like/unlike/reaction-change
  runs a single aggregate-recompute-and-UPDATE pass again, not two.
- **The 5+-`wrong` auto-flag logic moved, not disappeared.** It used to
  live in `models.py`'s `update_reaction_counts` (`moderation_status =
  'flagged'` once a post accumulates 5+ `wrong` reactions); now that
  receiver is gone, the same check has been folded into `signals.py`'s
  `sync_post_reaction_counts` — using the `wrong_count` already computed
  in that function's own aggregate query, and written in the same
  `UPDATE` rather than a second one. Behavior is unchanged (still only
  ever *sets* `flagged`, never auto-clears it); only where the logic
  lives changed.
- **Video thumbnail generation is no longer here** — see the "TASK 27"
  comment block in the code above. `PostMedia`'s `post_save` now only
  triggers `post.signals.queue_video_thumbnail_on_create`, which enqueues
  `post.tasks.generate_video_thumbnail` (Celery) instead of running ffmpeg
  inline. Storage-agnostic (works on local disk and S3/GCS) since it reads
  the file via the storage API, not `.path`. Full details in §19 Addendum 4
  and in `services.py`/`signals.py`/`tasks.py`'s own docstrings.
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
- **NEW (G-3):** `PostDetailSerializer.get_comments()` now consumes
  `user_profile.RestrictUser` for the first time — a comment from a user
  the *post owner* has restricted is excluded from the inline preview
  list of top-level comments, at the queryset level (before the `[:10]`
  slice, so restricted comments don't crowd out visible ones from the
  page). Restrict is checked against `obj.user_id` (the post owner), not
  the viewer — matching restrict's "protects the owner's space" semantics
  documented on `RestrictUser` itself (user_profile/models.py). A
  restricted user still sees their own comments normally when they view
  the post themselves (`restricted_ids.discard(viewer_id)`). Lazy-imports
  `user_profile.models.RestrictUser` inside the method rather than at
  module level, so `post` doesn't take a hard import-time dependency on
  `user_profile` — same pattern already used elsewhere for cross-app
  lookups (see the comment in the code above). This is the first real
  consumer of `RestrictUser.is_restricted_between()`'s underlying data
  from outside `user_profile` itself — previously flagged in
  `user_profile_app_reference.md` §11 as "restrict's effects are not
  consumed anywhere." Comment *visibility* elsewhere (the full comment
  endpoints in `comment_view.py`/§7, `CommentListAPIView`, etc.) is
  **not** touched by this change — only the inline preview on
  `PostDetailSerializer` is restrict-aware so far.

---

## 5. `comment_serializers.py` (full code — active version)

> Same situation as §4: the uploaded file has an entire earlier draft
> commented out at the top (dead code — Python never executes it). Only
> the **active code below** is included here — it's what actually runs.
> The real difference versus that draft: the active `PostCommentSerializer`
> adds **comment-level reactions** (`my_reaction`/`myReaction`,
> `reaction_counts`/`reactionCounts`, via `CommentLike`), which the draft
> didn't have. *(v-sync fix: an earlier revision of this doc accidentally
> pasted the dead draft into this code block too, ahead of the real code —
> fixed here; the block below is exactly and only the active file.)*

```python
# post/comment_serializers.py


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
    ReactionRequestSerializer,
)
from.signals import decrement_posts_count_on_soft_delete
from.services import notify_post_liked
from user_profile.models import Follow

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
# TASK 25 — dev-only fallback. Only ever mounted when
# `settings.SERVE_MEDIA_VIA_DJANGO` is True (see post/urls.py and
# settings.py's SERVE_MEDIA_VIA_DJANGO comment) — in production, media is
# served by nginx straight off disk (deploy/nginx.conf) or by S3/CloudFront
# (deploy/S3_CLOUDFRONT_SETUP.md), never through this view.
def serve_media_with_range(request, path):
    if settings.USE_S3_STORAGE:
        # Shouldn't be reachable — settings.py raises ImproperlyConfigured
        # at startup if SERVE_MEDIA_VIA_DJANGO=true and USE_S3_STORAGE=true
        # together. Fail loudly instead of a confusing "file not found" if
        # someone still manages to wire this route in anyway (e.g. a
        # `re_path` added by hand elsewhere): with S3 storage active,
        # MEDIA_ROOT is not where uploaded files live.
        raise Http404("Media is served from S3/CloudFront when USE_S3_STORAGE=True, not local disk.")
    # 🔒 FIX — path-traversal check moved BEFORE any filesystem access.
    # It previously ran *after* `os.path.exists()`/`os.path.isfile()` on
    # the unvalidated path, which let an attacker use response timing/
    # behavior as an existence oracle for arbitrary paths (e.g.
    # `../../.env`) before the traversal guard ever fired. Reject first,
    # touch disk second.
    if '..' in path or path.startswith('/'):
        raise Http404("Invalid path")
    file_path = os.path.join(settings.MEDIA_ROOT, path)
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        raise Http404("File not found")
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
# 🔥 TASK 22 — consolidated. This file used to define its own local
# `ReactionRequestSerializer` (identical `choices` list to the one in
# `serializers.py`, just single- vs double-quoted — no actual drift, so
# safe to collapse) instead of importing the canonical one. Now imported
# from `.serializers` above, like every other serializer this view uses.
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
            # Task 11 fix — only a genuinely new like notifies; a
            # reaction *change* (the `existing.reaction_type != reaction_type`
            # branch above) intentionally does not, same as unlike doesn't.
            notify_post_liked(post, user)

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
- ✅ **RESOLVED (TASK 22)** — `PostReactionAPIView` used to define a
  **local** `ReactionRequestSerializer` identical in shape to the one in
  `serializers.py` (§4). Now imported from `.serializers` like every
  other serializer this file uses; the local copy is gone. Closes §14
  issue #9.
- ✅ **TASK 11** — `PostReactionAPIView.post()` now calls
  `notify_post_liked(post, user)` (imported from `.services`) on a
  genuinely new like only — not on unlike, and not on a reaction-type
  change. See §19 Addendum 4 for the full notification-wiring story.
- ✅ **TASK 25** — `serve_media_with_range()` is a plain Django view
  function (not DRF) — wired in `urls.py` only when
  `settings.SERVE_MEDIA_VIA_DJANGO` is `True` (see §8; this replaces the
  old bare `settings.DEBUG` gate). It also now raises `Http404` up front
  if `settings.USE_S3_STORAGE` is `True` (shouldn't be reachable if
  settings.py enforces the two being mutually exclusive), and the
  path-traversal check (`'..' in path`) now runs **before** any
  filesystem access instead of after — see the function's own comments
  and §19.3. Still a local-dev/small-scale convenience even so: no
  auth/permission checks of its own, every request re-reads the file
  from disk in a Python worker, and there's no CDN/shared cache in front
  of it. Real production media serving is nginx or S3/CloudFront.

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
from.services import notify_post_commented

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

        # Task 24 CORRECTION — see models.py's own note (search
        # "update_comments_count REMOVED ENTIRELY") for why this manual
        # F() update belongs here and NOT in a signal. A previous pass on
        # this file did the opposite — added a signal-based
        # `update_comments_count` and removed this manual update — which
        # re-introduces exactly the bug models.py documents fixing: this
        # is a soft-delete-based app, so a signal recomputing/adjusting
        # `comments_count` on every PostComment save can't distinguish
        # "new comment", "content edit", and "hide/unhide toggle" without
        # a lot of fragile state-tracking, whereas the manual +1/-1 here
        # (mirrored by CommentDeleteAPIView's -1) is simple and already
        # correct — same pattern `replies_count` has always used safely.
        # `post/signals.py` deliberately has NO PostComment receiver.
        if parent:
            PostComment.objects.filter(id=parent.id).update(replies_count=F('replies_count') + 1)
        else:
            Post.objects.filter(id=post.id).update(comments_count=F('comments_count') + 1)
            # Task 11 fix — only a new TOP-LEVEL comment notifies the post
            # owner (matches notify_post_commented()'s own docstring); a
            # reply to another comment doesn't spam the post owner for
            # every sub-thread reply.
            notify_post_commented(post, comment)

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

        # Task 13 fix — re-check is_comments_disabled here too, not just
        # at init(). init's check (see chunked_upload_init's own FIX
        # comment above) only guards the START of what can be a
        # long-running, multi-request upload (up to 4GB) — the post
        # owner can flip is_comments_disabled at any point during that
        # window, and complete() used to resolve post/parent (and create
        # the comment) without ever looking at the flag again. Resolved
        # and checked here, BEFORE assembling the chunks into the final
        # file (not after), so a now-blocked upload fails cheaply instead
        # of first paying the disk I/O to stitch together a multi-GB file
        # it's about to reject anyway. `post`/`parent` are reused below
        # instead of being re-queried a second time after assembly.
        if upload.parent_id:
            parent = get_object_or_404(PostComment, id=upload.parent_id)
            post = parent.post
        else:
            parent = None
            post = get_object_or_404(Post, id=upload.post_id)
        if post.is_comments_disabled:
            return Response({"error": "Comments disabled"}, status=403)

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

        # Task 24 CORRECTION — same reasoning as CommentCreateAPIView.post()
        # above: the manual F() update belongs here (matches models.py's
        # documented decision), there is no comments_count signal.
        if parent:
            PostComment.objects.filter(id=parent.id).update(replies_count=F('replies_count') + 1)
        else:
            Post.objects.filter(id=post.id).update(comments_count=F('comments_count') + 1)
            # Task 11 fix — same top-level-only notify as the regular
            # CommentCreateAPIView path above; a large video comment
            # finished via chunked upload is still a new top-level
            # comment and should notify the post owner the same way.
            notify_post_commented(post, comment)

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
        # Task 24 CORRECTION — restored. models.py explicitly documents
        # removing the old `update_comments_count` signal *because* this
        # manual decrement (mirroring the manual +1 in
        # CommentCreateAPIView) is the correct, intended mechanism — see
        # that file's "update_comments_count REMOVED ENTIRELY" note.
        # CommentHideAPIView's own separate manual comments_count
        # adjustment (below) is unrelated — it toggles is_hidden, not
        # is_deleted — and stays exactly as-is.
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
- ✅ **TASK 24 CORRECTION** — an earlier draft of this doc (and, for a
  while, the actual code) assumed a `models.py` signal called
  `update_comments_count` recomputed `Post.comments_count` on every
  `PostComment` save/delete, making `CommentCreateAPIView`'s /
  `CommentDeleteAPIView`'s manual `F('comments_count') ± 1` calls merely
  redundant with it. That signal **no longer exists** — see §3's
  models.py, "`update_comments_count` REMOVED ENTIRELY" — because it was
  a real correctness bug, not just redundant work: it fired on every
  save (create, edit, hide/unhide) and couldn't tell those apart from a
  genuine create/soft-delete, so combined with the manual F() updates it
  double-counted in both directions. The manual F() update here (and in
  `CommentCreateAPIView`, and in `chunked_upload_complete`) is now the
  **only** mechanism keeping `comments_count` accurate — `post/signals.py`
  deliberately has no `PostComment` receiver at all. Same pattern
  `replies_count` has always safely used.
- `CommentHideAPIView`'s manual `comments_count ∓1` on hide/unhide is
  unrelated to the above — it toggles `is_hidden`, not `is_deleted` — and
  is unaffected by the TASK 24 correction; it was never redundant with
  anything, and stays exactly as it was.
- ✅ **TASK 11** — `CommentCreateAPIView.post()` and
  `chunked_upload_complete()` now both call `notify_post_commented(post,
  comment)` (imported from `.services`), but **only** on the branch where
  the new comment is top-level (`parent is None`) — a reply to another
  comment doesn't notify the post owner. See §19 Addendum 4.
- ✅ **TASK 13** — `chunked_upload_complete()` now re-checks
  `post.is_comments_disabled` itself, immediately after resolving
  `post`/`parent` and *before* assembling the chunks into the final file.
  Previously only `chunked_upload_init()` checked the flag, so a post
  owner flipping `is_comments_disabled` mid-upload (uploads can span
  multiple requests over a long window for files up to 4GB) had no
  effect on an upload already in progress.
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

# TASK 25 — production media serving.
#
# ⚠️ `serve_media_with_range` has no auth/permission checks of its own
# (see views.py notes), streams every request through a Python worker
# instead of the webserver's sendfile path, and has zero CDN/shared-cache
# in front of it. It is a local-dev convenience, not a production media
# server.
#
# Gated on `settings.SERVE_MEDIA_VIA_DJANGO` rather than bare `DEBUG` so
# ops can tell at a glance (in settings.py) exactly when this route is
# live, and so a production box that hasn't wired up nginx yet 404s
# loudly on `/media/` instead of silently working via this fallback.
# Real production media serving is nginx (`deploy/nginx.conf`, local-disk
# storage) or S3/CloudFront (`deploy/S3_CLOUDFRONT_SETUP.md`, when
# `USE_S3_STORAGE=true` — that path never even reaches this route, since
# `default_storage.url()` already points straight at S3/CloudFront).
if settings.SERVE_MEDIA_VIA_DJANGO:
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
- ✅ **RESOLVED (TASK 25):** `serve_media_with_range` used to be gated on
  bare `DEBUG=True`; it's now gated on its own `settings.
  SERVE_MEDIA_VIA_DJANGO` flag (see the code above and §2). In
  production you must still serve `MEDIA_URL` some other way (nginx
  `X-Accel-Redirect`, S3 signed URLs, a CDN, etc.) — this view remains a
  local-dev convenience either way, just with a clearer, purpose-built
  flag gating it instead of overloading `DEBUG`.
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

✅ **RESOLVED:** `ChunkedUpload` and `CommentLike` (both defined in
`models.py`) **are now registered** — see `ChunkedUploadAdmin` and
`CommentLikeAdmin` in the code above. This doc used to note them as
unregistered; that gap is closed (along with `Story`/`StoryView`, also
newly registered above). Nothing further to do here.

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

## 10.1 `services.py` (full code — renamed from uploaded `Services.py`)

> Case-sensitivity rename (same reasoning as §10's `apps.py` note —
> `import post.signals`/`.tasks`/`.services` are lowercase, so a
> capitalized `Services.py` silently only worked on case-insensitive dev
> filesystems). Also carries the TASK 11 notification-wiring fix (real
> `core.services.create_notification` module + matching call signature)
> and the storage-agnostic ffmpeg helpers (task 27). Full rationale for
> every change already lives in this file's own module docstring below
> and in Addendum 2 (§17.1) / Addendum 4 (§19) — this section exists so
> the literal, current code has one canonical home instead of only being
> quoted in fragments across those addenda.

```python
"""
post/services.py

⚠️ RENAMED from the uploaded `Services.py` — apps.py does
`import post.signals` (lowercase), and this module gets imported the same
way. On a case-sensitive filesystem (Linux/prod) a capitalized
`Services.py` / `Signals.py` / `Tasks.py` is a DIFFERENT file to Python
than `services.py` / `signals.py` / `tasks.py` — the import would raise
`ModuleNotFoundError` at runtime. It only "worked" by accident on
case-insensitive dev filesystems (Windows/macOS default). Same rename
applied to signals.py and tasks.py.

⚠️ CRITICAL FIX — the uploaded file imported `Hashtag`, `PostHashtag`,
`Like`, `SavedPost`, `Comment` from `.models`. None of these exist.
Real models.py has: Post, PostMedia, PostLike, PostComment, CommentMedia,
PostShare, PostView, PostSave, ChunkedUpload, CommentLike.

- `attach_hashtags()` / the whole Hashtag/PostHashtag idea — REMOVED.
  Hashtags aren't a separate model here: `Post.hashtags` is a plain
  `JSONField(default=list)`, populated directly inside
  `PostCreateSerializer.create()` via `re.findall(r"#(\w+)", content)`
  (see serializers.py, and post_app.md §13.1). There's nothing left for a
  service function to do.
  ⚠️ serializers.py currently STILL has a dead/broken call —
      hashtags = validated_data.get("hashtags")
      if hashtags:
          from .services import attach_hashtags
          attach_hashtags(post, hashtags)
  left over from this same wrong assumption. That will raise
  ImportError/AttributeError the moment anyone creates a post with
  hashtags, since this function no longer exists (and shouldn't).
  Delete those lines from `PostCreateSerializer.create()` —
  `validated_data["hashtags"]` is already what gets saved on the row.
- `toggle_like()` / `toggle_save()` / `add_comment()` / `delete_comment()`
  — REMOVED. These duplicated logic already implemented, correctly,
  directly in views.py / comment_view.py against the real models
  (`PostLike` / `PostSave` / `PostComment`), with counters kept in sync by
  the `@receiver` signals already living in models.py
  (`update_reaction_counts`, `update_saves_count`, etc — see models.py's
  own "NOTE (fix...)" comment on why a second counter-update path is a
  correctness bug, not just redundant work). Nothing in this app calls
  `services.toggle_like` etc. today — keeping them as dead code against
  nonexistent models was the actual problem, not a missing feature.

What's kept below is genuinely additive — logic the views/signals don't
already provide:

- The `core.create_notification` hookup (checklist item 63 / Phase 3's
  hub) — fixed to use `post.user` (the real FK) instead of the
  nonexistent `post.author`, and to take a `PostComment` instance instead
  of the nonexistent `Comment`.
  ⚠️ TASK 11 FIX — this used to probe `core.notifications.create_notification`,
  a module that doesn't exist (the real function is
  `core.services.create_notification`), so the probe's `except ImportError`
  always fired and every call silently fell through to the debug-log
  no-op stub below — no bell row was ever created, even after this
  function started being called. On top of that, the stub's own
  signature (`recipient, actor, verb, target_type, target_id, payload`)
  never matched the real `core.services.create_notification`'s signature
  (`recipient, notif_type, title, message=None, data=None` — see
  `core/tests.py` for confirmed call shapes), so fixing only the import
  path would have raised a `TypeError` on the very first real call.
  Both fixed below: the import now points at `core.services`, and
  `notify_post_liked`/`notify_post_commented` build the
  (notif_type, title, message, data) shape that function actually
  expects, using the new `Notification.NotifType.POST_LIKED`/
  `POST_COMMENTED` choices added in `core/models.py`.
  Now wired: `notify_post_liked` is called from
  `PostReactionAPIView.post()` (only the `status_msg == "liked"` branch)
  and `notify_post_commented` from `CommentCreateAPIView.post()` (only
  for new top-level comments, i.e. `parent is None`) — see those files.
- `share_post_to_conversation()` (checklist item 61) — fixed to use
  `post.user` / `post.content` instead of `post.author` / `post.caption`,
  and `PostShare.objects.get_or_create` instead of `.create()` (the real
  model has `unique_together = ['post', 'user']`, so a second share by
  the same user would raise `IntegrityError`). Also stopped hand-rolling
  a `Post.share_count` F()-update against a field that doesn't exist —
  the real counter is `shares_count`, already kept in sync by
  `update_shares_count` in models.py whenever a `PostShare` row is
  created. Still unwired — no view/url calls this yet (see urls.py: no
  `/share/` route exists). Add one once the `message` app's real
  send-function path is confirmed.
"""
import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TASK 27 — cloud-storage-safe file access for external binaries (ffmpeg,
# and anything else that needs a real local path: virus scanners, image
# processors, etc).
#
# The old `auto_generate_video_thumbnail` read `instance.file.path`
# directly. `.path` only exists for `FileSystemStorage` — it raises
# `NotImplementedError` on `storages.backends.s3.S3Storage` (there is no
# local filesystem path for a remote object), and the old code caught
# that failure with a bare `print()` instead of `logger`, so the moment
# `USE_S3_STORAGE=true` (task 26) was flipped on, thumbnail generation
# started silently no-op-ing for every video with nothing showing up in
# Sentry/logs to say why.
#
# `.open("rb")` + chunked read, below, works identically for every
# storage backend Django/django-storages supports — local disk today,
# S3 after task 26, GCS/Azure if this ever moves again — because it goes
# through the storage API instead of assuming a local filesystem.
# ---------------------------------------------------------------------------
def download_storage_file_to_temp(file_field, suffix=""):
    """Copy a Django FileField's content to a local NamedTemporaryFile,
    regardless of which storage backend is behind it, and return the
    local path. ffmpeg (and most other external binaries) need an actual
    path on disk to read from — they have no concept of S3/GCS.

    Caller owns the returned path and MUST delete it (e.g. in a
    `finally:` block) once done — this function only creates it.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        with file_field.open("rb") as src:
            for chunk in src.chunks():
                tmp.write(chunk)
    finally:
        tmp.close()
    return tmp.name


def generate_video_thumbnail_file(video_path, time_offset="00:00:01", timeout=30):
    """Run ffmpeg against a LOCAL video file path (already downloaded via
    `download_storage_file_to_temp` above — ffmpeg has no concept of S3)
    and return the local path to a generated JPEG thumbnail, or `None` if
    generation failed for any reason. Every failure path is logged via
    `logger` (not `print()`, task 27's other reported gap) so a bad
    upload or a missing ffmpeg binary actually shows up in production
    logs/Sentry instead of silently vanishing.

    Caller owns the returned path and MUST delete it once done, same as
    `download_storage_file_to_temp`.
    """
    if shutil.which("ffmpeg") is None:
        # Infra problem (ffmpeg not installed in the app image/container),
        # not a per-file problem — log once per call so it's loud in
        # aggregated logs, but don't raise: one video with no thumbnail
        # yet is a much better failure mode than crashing the upload.
        logger.error(
            "ffmpeg binary not found on PATH — cannot generate video "
            "thumbnails. Install ffmpeg in the app image/container."
        )
        return None

    thumb_fd, thumb_path = tempfile.mkstemp(suffix=".jpg")
    os.close(thumb_fd)  # ffmpeg writes the actual bytes; we only needed the path

    cmd = [
        "ffmpeg", "-y",
        "-ss", time_offset,
        "-i", video_path,
        "-frames:v", "1",
        "-vf", "scale=480:-1",
        thumb_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg timed out (%ss) generating thumbnail for %s", timeout, video_path)
        if os.path.exists(thumb_path):
            os.unlink(thumb_path)
        return None
    except OSError as exc:
        # e.g. ffmpeg binary present in `which` but not actually executable,
        # or disappeared between the check above and this call.
        logger.exception("ffmpeg failed to start for %s: %s", video_path, exc)
        if os.path.exists(thumb_path):
            os.unlink(thumb_path)
        return None

    if result.returncode != 0 or not os.path.exists(thumb_path) or os.path.getsize(thumb_path) == 0:
        logger.error(
            "ffmpeg failed generating thumbnail for %s (rc=%s): %s",
            video_path,
            result.returncode,
            result.stderr.decode(errors="replace")[:500] if result.stderr else "",
        )
        if os.path.exists(thumb_path):
            os.unlink(thumb_path)
        return None

    return thumb_path

# ---------------------------------------------------------------------------
# Notification hookup (checklist item 63 / Phase 3's hub).
#
# TASK 11 FIX: `core.notifications` never existed — the real module is
# `core.services`, and its `create_notification()` takes
# `(recipient, notif_type, title, message=None, data=None)`, not the
# `(recipient, actor, verb, target_type, target_id, payload)` shape this
# file's fallback stub used to have. Both are fixed below. The
# `except ImportError` guard is kept (not because `core.services` is
# expected to be missing — it isn't, `core` is a required app — but so
# this app degrades to a logged no-op instead of a hard crash on every
# like/comment in the unlikely event the `core` app isn't installed in a
# given environment, e.g. a stripped-down test settings module).
# ---------------------------------------------------------------------------
try:
    from core.services import create_notification as _create_notification_row
except ImportError:  # pragma: no cover - only if the `core` app isn't installed
    def _create_notification_row(recipient, notif_type, title, message=None, data=None):
        logger.debug(
            "core.services.create_notification not available — skipping notification "
            "(%s: %s -> %s)", notif_type, title, recipient,
        )
        return None


# PRODUCTION FIX — `notify_post_liked`/`notify_post_commented` used to do
# `from core.models import Notification` as an *unguarded* local import.
# If `core` genuinely isn't installed in some environment (the exact case
# the try/except above claims to handle gracefully), that unguarded
# import raised ImportError straight out of every single like and every
# top-level comment — i.e. it crashed the two hottest write paths in this
# app, which is a much worse outcome than the "log + no-op" the module
# docstring promises. Guarded the same way as `_create_notification_row`
# above, so `core` being absent degrades this to a no-op everywhere, not
# just in the create_notification call itself.
try:
    from core.models import Notification as _Notification
except ImportError:  # pragma: no cover - only if the `core` app isn't installed
    _Notification = None


def notify_post_liked(post, actor):
    """Call from PostReactionAPIView.post(), only on the branch where a new
    PostLike was just created (status_msg == 'liked') — not on unlike or
    reaction-change."""
    if post.user_id == actor.id:
        return  # don't notify yourself
    if _Notification is None:
        return  # `core` app not installed — nothing to notify with

    actor_name = actor.get_full_name() or actor.username
    _create_notification_row(
        post.user,
        _Notification.NotifType.POST_LIKED,
        f"{actor_name} liked your post",
        data={"post_id": str(post.id), "actor_id": str(actor.id)},
    )


def notify_post_commented(post, comment):
    """Call from CommentCreateAPIView.post() after a new top-level
    PostComment is created. `comment` is a PostComment instance."""
    if post.user_id == comment.user_id:
        return
    if _Notification is None:
        return  # `core` app not installed — nothing to notify with

    actor_name = comment.user.get_full_name() or comment.user.username
    _create_notification_row(
        post.user,
        _Notification.NotifType.POST_COMMENTED,
        f"{actor_name} commented on your post",
        (comment.content or "")[:200],
        data={"post_id": str(post.id), "comment_id": str(comment.id)},
    )


# ---------------------------------------------------------------------------
# Share a post into a chat conversation (checklist item 61).
#
# Reuses the `message` app's existing attachment-message flow rather than
# reimplementing message-sending. The exact function name/signature in
# `message` wasn't visible when this was written — the call below is a
# best-guess based on the message-app's documented flow
# (`MessageViewSet`, attachment upload via `upload_view.py`). ADJUST the
# import + call to match your actual `message/services.py` (or wherever
# send-message logic lives) once you wire this up — everything else here
# stays the same.
# ---------------------------------------------------------------------------
def share_post_to_conversation(post, sender, conversation_id):
    """Raises NotImplementedError with a clear message if the message app's
    send function isn't available yet, so this fails loudly instead of
    silently doing nothing."""
    from .models import PostShare

    try:
        from message.services import send_message  # ADJUST to your real path
    except ImportError as exc:
        raise NotImplementedError(
            "message.services.send_message not found — wire this to your "
            "actual message-sending function (see comment in post/services.py)."
        ) from exc

    first_media = post.media.first()
    attachment_url = first_media.file.url if first_media else None

    message = send_message(
        conversation_id=conversation_id,
        sender=sender,
        text=post.content or "",
        shared_post_id=str(post.id),
        attachment_url=attachment_url,
    )

    # unique_together=['post', 'user'] on PostShare, mirroring how
    # PostLike/PostSave behave — get_or_create so re-sharing the same post
    # doesn't raise IntegrityError. `shares_count` updates itself via the
    # `update_shares_count` signal in models.py; no manual F() needed here.
    PostShare.objects.get_or_create(post=post, user=sender)

    return message
```

Interconnections this file owns:
- **`core.services.create_notification`** — `notify_post_liked()` /
  `notify_post_commented()` call straight into the `core` app's real
  notification pipeline (guarded by a lazy `try/except ImportError` so
  `post` degrades to a logged no-op rather than crashing if `core` is
  ever absent from an environment). Called from `PostReactionAPIView`
  (on the `liked` branch only) and `CommentCreateAPIView` (top-level
  comments only) — see §6/§7.
- **`core.models.Notification.NotifType.POST_LIKED`/`POST_COMMENTED`** —
  the two choices this app depends on existing on `core`'s side.
- **`message.services.send_message`** (`share_post_to_conversation`) —
  **best-guess path, not yet confirmed against the real `message` app**,
  and **not wired to any view/url yet** (no `/share/` route exists in
  §8). Adjust the import once the real function is confirmed, then add
  a route + view calling it.
- **ffmpeg** (external binary, not a Python package) — both thumbnail
  helpers shell out to it via `subprocess`; `tasks.py` (§10.3) is the
  only caller.

---

## 10.2 `signals.py` (full code — renamed from uploaded `Signals.py`)

> Case-sensitivity rename, same as `services.py` above. Registered from
> `apps.py`'s `ready()` (§10). Owns two independent pieces of counter
> bookkeeping — `User.posts_count` and `Post`'s reaction counts — plus
> the fire-and-forget enqueue of video-thumbnail generation **and (TASK
> 3, new)** the fire-and-forget enqueue of the new-post follower-notify
> fan-out. TASK 23 / fix B-5 (deduping the old two-receiver
> reaction-count split — see this file's own docstring, and models.py's
> matching removal note) is now fully resolved: `models.py` no longer
> has a competing `update_reaction_counts` receiver;
> `sync_post_reaction_counts` here is the single source of truth.
>
> **TASK 3 (new):** a third `post_save` receiver on `Post`,
> `queue_new_post_notification_fanout`, enqueues
> `tasks.notify_followers_new_post` for every newly-created `Post` —
> via `transaction.on_commit(...)` rather than a bare `.delay()` (unlike
> `queue_video_thumbnail_on_create` below, which still uses a bare
> `.delay()`), so the task can't run before the `Post` row's own
> transaction has actually committed. See §22 for the full writeup.

```python
"""
post/signals.py

⚠️ RENAMED from the uploaded `Signals.py` — apps.py does
`import post.signals` in lowercase, which fails to resolve on a
case-sensitive filesystem (Linux/prod) against a file literally named
`Signals.py`. Same rename applied to services.py / tasks.py.

⚠️ FIX — every function here used `instance.author_id`. `Post` has no
`author` field/FK — the real one is `Post.user` (see models.py). This
raised `AttributeError` the first time any of these receivers fired.

DECISION — `posts_count` is now kept in sync HERE, via
`post_save`/`post_delete`, instead of the manual
`User.objects.filter(...).update(posts_count=F('posts_count') + 1)` line
that used to live inline in `PostCreateAPIView.post()`. That manual line
has been removed from views.py to match (see views.py's own note at that
call site) — keeping both would double-count.

Signal-based wins for production: it's the single place this logic lives
no matter which code path creates/deletes a Post (the API view, the admin,
a management command, a data-migration script, a test calling
`Post.objects.create()` directly) — a future second entry point into post
creation can't silently forget to bump the counter, because it was never
its job to remember in the first place. This also matches what checklist
item 58 originally asked for ("Post.save() signal se posts_count update
karo").

KEPT — `decrement_posts_count_on_soft_delete`, called explicitly from the
new `PostDeleteAPIView.delete()` in views.py (soft-delete never fires
`post_delete`, so it can't be a signal). `decrement_posts_count_on_hard_delete`
is registered for whenever/if a genuine hard-delete path is ever added
(e.g. an admin purge command) — inert today, harmless to leave wired up.

🔥 TASK 23 — `PostLike` used to have two separate signal handlers
(`update_likes_count`, `update_reaction_counts`) both firing on every
PostLike save/delete and both writing `Post.likes_count` independently.
Beyond the redundant writes, that split was a correctness risk: a
*reaction change* (`PostReactionAPIView.post()` does `existing.
reaction_type = new_type; existing.save()` — same row, not a create or
delete) doesn't move the total (`likes_count`), only the per-type
breakdown (`like_count`/`confuse_count`/`wrong_count`/`imp_count`/
`explain_count`) — nothing guaranteed both handlers agreed on how to
treat that case, and an incremental `F(...) + 1`/`- 1` style counter
only even makes sense on create/delete in the first place.

Replaced both with `sync_post_reaction_counts` below: a single receiver
on PostLike's `post_save`/`post_delete` that recomputes every reaction
count directly from the actual `PostLike` rows via one aggregate query,
then writes all of them in one `UPDATE`. An aggregate recompute can't
drift out of sync the way two independent incremental counters can, and
it's naturally correct for create, delete, *and* the in-place reaction
change case, with no special-casing needed for any of the three.
"""
import logging

from django.db.models import Count, F, Q
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Post, PostLike, PostMedia

logger = logging.getLogger(__name__)


def _user_model():
    from django.contrib.auth import get_user_model

    return get_user_model()


@receiver(post_save, sender=Post)
def increment_posts_count_on_create(sender, instance, created, **kwargs):
    if not created:
        return
    User = _user_model()
    if not hasattr(User, "posts_count"):
        logger.warning("User model has no `posts_count` field — skipping sync.")
        return
    User.objects.filter(pk=instance.user_id).update(posts_count=F("posts_count") + 1)


@receiver(post_delete, sender=Post)
def decrement_posts_count_on_hard_delete(sender, instance, **kwargs):
    """Only fires on a genuine hard delete (a queryset/instance `.delete()`
    that actually removes the row) — the normal delete path in this app is
    the soft-delete below, which never triggers post_delete."""
    User = _user_model()
    if not hasattr(User, "posts_count"):
        return
    User.objects.filter(pk=instance.user_id).update(posts_count=F("posts_count") - 1)


def decrement_posts_count_on_soft_delete(post):
    """Not a Django signal — soft-delete is just a `.save()`/`.update()`
    under the hood and won't fire `post_delete`. Called explicitly from
    `PostDeleteAPIView.delete()` in views.py, mirroring the exact pattern
    `CommentDeleteAPIView` already uses for `PostComment` counters."""
    User = _user_model()
    if not hasattr(User, "posts_count"):
        return
    User.objects.filter(pk=post.user_id).update(posts_count=F("posts_count") - 1)


# ----------------------------------------------------------------------
# TASK 3 — "new post from someone you follow" fan-out, enqueue-only.
#
# Same reasoning as queue_video_thumbnail_on_create further down:
# `post_save` runs synchronously inside whatever request/transaction
# created this `Post` row (`PostCreateAPIView.post()`), and a popular
# account's follower list can run into the thousands — looping through
# even a cheap per-follower write inline here would make every single
# post-create request slow in direct proportion to that account's
# follower count. `.delay()` just enqueues
# `post.tasks.notify_followers_new_post` and returns immediately; the
# actual `Follow` table query and the notification fan-out itself happen
# there, off the request path.
#
# transaction.on_commit(...) (deliberately NOT a bare `.delay()` the way
# queue_video_thumbnail_on_create below still is): if
# PostCreateAPIView.post() ever wraps the Post creation in
# `@transaction.atomic` (as several views in this codebase already do —
# e.g. FollowAPIView.post()), a bare `.delay()` fired from inside that
# transaction could have the Celery worker pick up the task and query
# for this Post row before the transaction actually commits, raising
# Post.DoesNotExist in the task for a post that does, in fact, exist.
# on_commit() defers the enqueue until the surrounding transaction (if
# any) has successfully committed — and runs immediately, synchronously,
# if there's no open transaction at all (autocommit), so this is strictly
# safer with no downside either way.
#
# MVP scope (per the design doc): every ACCEPTED follower gets notified
# on every new post — no per-follower "bell" opt-in yet (Instagram-style,
# per-account). That's a deliberate, documented trade-off for a later
# pass, not something this receiver is trying to solve.
# ----------------------------------------------------------------------
@receiver(post_save, sender=Post)
def queue_new_post_notification_fanout(sender, instance, created, **kwargs):
    if not created:
        return
    from django.db import transaction

    from .tasks import notify_followers_new_post

    transaction.on_commit(lambda: notify_followers_new_post.delay(instance.id))


# ----------------------------------------------------------------------
# TASK 23 — PostLike reaction counts (see module docstring for why this
# replaces the old `update_likes_count` / `update_reaction_counts` pair).
# ----------------------------------------------------------------------
# Must stay in sync with `ReactionRequestSerializer.reaction`'s
# `choices` (serializers.py) — that's the only other place this set of
# reaction types is spelled out, and each entry here maps directly to a
# `Post.<type>_count` field.
REACTION_TYPES = ("like", "confuse", "wrong", "imp", "explain")


def sync_post_reaction_counts(post_id):
    """
    Single source of truth for a `Post`'s reaction counters. Recomputes
    every per-type count (`like_count`, `confuse_count`, `wrong_count`,
    `imp_count`, `explain_count`) plus the `likes_count` total straight
    from `PostLike` rows, in one aggregate query, then writes all of
    them (plus the auto-flag below, when it applies) in one `UPDATE` —
    so the two never disagree the way two separately-maintained
    incremental counters could.

    Not `@receiver`-decorated itself (that's `_on_save`/`_on_delete`
    below) so it can also be called directly wherever `PostLike` rows
    might be touched outside a normal save/delete — e.g. a future
    moderation bulk-remove or a data-migration backfill — the same way
    `decrement_posts_count_on_soft_delete` above is called explicitly
    for its own out-of-band case.

    Trade-off, noted deliberately: this is a full recompute (one
    `COUNT`-style aggregate) rather than an incremental +1/-1, which
    costs one extra query per like/unlike compared to the old approach.
    That's the right trade for a reaction feature — a post's total like
    count staying wrong is a worse bug than one more cheap indexed
    COUNT — but if a single post's `PostLike` volume ever gets large
    enough for this to matter, the field to revisit is scale on this
    query, not going back to incremental counters.

    FIX (B-5) — this used to be duplicated by a second receiver,
    `update_reaction_counts` in models.py, which independently
    recomputed the same counts AND carried its own 5+-`wrong`
    auto-flag-to-`flagged` check as a *second* `UPDATE` right after the
    first. Both receivers were registered on the same PostLike
    post_save/post_delete signals, so every like/unlike paid for two
    full aggregate-recompute + UPDATE round trips converging on
    identical numbers — pure waste on the app's hottest write path.
    `update_reaction_counts` has been deleted from models.py; its
    auto-flag check is folded in here instead, using the `wrong_count`
    already sitting in `counts` (no extra query needed), and merged
    into the same `UPDATE` as the counts themselves rather than firing
    a second one. Matches the old behavior exactly: it only ever sets
    `flagged`, never clears it back once `wrong_count` drops below 5.
    """
    counts = PostLike.objects.filter(post_id=post_id).aggregate(
        **{f"{rt}_count": Count("id", filter=Q(reaction_type=rt)) for rt in REACTION_TYPES},
        likes_count=Count("id"),
    )
    if counts["wrong_count"] >= 5:
        counts["moderation_status"] = "flagged"
    Post.objects.filter(pk=post_id).update(**counts)


@receiver(post_save, sender=PostLike)
def sync_post_reaction_counts_on_save(sender, instance, **kwargs):
    """
    Deliberately does NOT branch on `created` the way
    `increment_posts_count_on_create` above does — a reaction *change*
    (`PostReactionAPIView.post()`: `existing.reaction_type = new_type;
    existing.save()`) is a save with `created=False` that still needs
    the per-type breakdown recomputed (old type's count -1, new type's
    +1 — even though the `likes_count` total doesn't move). Since
    `sync_post_reaction_counts` recomputes from scratch rather than
    incrementing, running it unconditionally on every save handles
    create AND change identically and correctly, with no special case.
    """
    sync_post_reaction_counts(instance.post_id)


@receiver(post_delete, sender=PostLike)
def sync_post_reaction_counts_on_delete(sender, instance, **kwargs):
    sync_post_reaction_counts(instance.post_id)


# ----------------------------------------------------------------------
# TASK 27 — video thumbnail generation, enqueue-only.
#
# This receiver's ONLY job is to hand off to Celery
# (`post.tasks.generate_video_thumbnail`) — it deliberately does not call
# ffmpeg or touch storage itself. `post_save` runs synchronously inside
# whatever request/transaction created this `PostMedia` row
# (`PostCreateAPIView.post()`); running ffmpeg (a slow subprocess against
# a real video file) inline here would block that request's response for
# however long ffmpeg takes, on every single video upload. `.delay()`
# just enqueues and returns immediately.
# ----------------------------------------------------------------------
@receiver(post_save, sender=PostMedia)
def queue_video_thumbnail_on_create(sender, instance, created, **kwargs):
    if not created or instance.media_type != "video":
        return
    from .tasks import generate_video_thumbnail

    generate_video_thumbnail.delay(instance.id)
```

Interconnections this file owns:
- **`django.contrib.auth.get_user_model()`** — writes `posts_count`
  directly onto whatever the project's `AUTH_USER_MODEL` is (the `login`
  app's `User` — see `login_app_reference.md` §3) via `F()` updates, the
  same atomic-update contract that `login`'s own model docstring asks
  every caller of `followers_count`/`following_count`/`posts_count`/
  `coin` to follow. Guarded with `hasattr(User, "posts_count")` so this
  app doesn't hard-crash if it's ever pointed at a `User` model without
  that field.
- **`post.tasks.generate_video_thumbnail`** — `queue_video_thumbnail_on_create`
  is the *only* place this Celery task gets enqueued (`.delay()`, on
  every new `PostMedia` row where `media_type == "video"`).
- **`post.tasks.notify_followers_new_post`** (TASK 3, new) —
  `queue_new_post_notification_fanout` is the *only* place this Celery
  task gets enqueued, via `transaction.on_commit(lambda: ...delay(...))`
  on every new `Post` row. See §10.3/§22.
- Must stay in sync with `ReactionRequestSerializer.reaction`'s
  `choices` in `serializers.py` (§4) — `REACTION_TYPES` here is the only
  other place that same 5-value set (`like`/`confuse`/`wrong`/`imp`/
  `explain`) is spelled out; if one changes, the other silently drifts.

---

## 10.3 `tasks.py` (full code — renamed from uploaded `Tasks.py`)

> Case-sensitivity rename, same as above. Two Celery Beat housekeeping
> tasks for `Story` expiry (checklist items 54/55/57/60) plus the async
> video-thumbnail task enqueued by `signals.py` above (task 27), **plus
> (TASK 3, new)** `notify_followers_new_post` — enqueued, not scheduled,
> triggered by `signals.py::queue_new_post_notification_fanout` on every
> new `Post`. Not wired into `settings.py` automatically — see the
> `CELERY_BEAT_SCHEDULE` snippet in this file's own docstring below;
> confirm it's actually present in the real `settings.py` (§2 doesn't
> currently list it). `notify_followers_new_post` needs no beat entry —
> it only ever runs enqueued, same as `generate_video_thumbnail`.

```python
"""
post/tasks.py

⚠️ RENAMED from the uploaded `Tasks.py` (case-sensitivity — see
services.py's docstring).

⚠️ PREVIOUSLY BLOCKED ON — a `Story` model that didn't exist anywhere in
this app. That's now added in models.py (checklist items 54/55/57/60),
so this task's original premise is valid again. Rewritten against the
real field names on the new `Story` model (`user`, not `author`;
`soft_delete()` is a real method now, not assumed).

Wire into settings.py CELERY_BEAT_SCHEDULE:

    CELERY_BEAT_SCHEDULE = {
        ...
        "expire-old-stories": {
            "task": "post.tasks.expire_old_stories",
            "schedule": crontab(minute="*/15"),  # every 15 min is plenty
        },
        "purge-ancient-stories": {
            "task": "post.tasks.hard_delete_ancient_stories",
            "schedule": crontab(hour=3, minute=0, day_of_week=0),  # weekly
        },
    }

Note this task is pure housekeeping, not a visibility gate: `Story`
listing endpoints already filter `expires_at__gt=timezone.now()` in
real time (see `StoryListAPIView.get_queryset()` in views.py), so an
expired-but-not-yet-soft-deleted story is already invisible to users
even before this task runs. This task's only job is to stop expired rows
piling up forever and to soft-delete them so their `StoryView` rows are
eventually eligible for cleanup too.
"""
import logging
import os
from datetime import timedelta

from celery import shared_task
from django.core.files import File
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def expire_old_stories():
    from .models import Story

    now = timezone.now()
    expired = Story.objects.filter(expires_at__lte=now, is_deleted=False)
    count = expired.count()
    for story in expired.iterator():
        story.soft_delete()
    logger.info("expire_old_stories: soft-deleted %s expired stories", count)
    return count


@shared_task
def hard_delete_ancient_stories(days=30):
    """Permanently remove stories soft-deleted more than `days` ago, so the
    DB doesn't grow forever with dead rows. Run weekly — not required for
    correctness (listing/visibility never depends on this task running)."""
    from .models import Story

    cutoff = timezone.now() - timedelta(days=days)
    old = Story.objects.filter(is_deleted=True, deleted_at__lte=cutoff)
    count = old.count()
    old.delete()
    logger.info("hard_delete_ancient_stories: purged %s stories older than %sd", count, days)
    return count


# ---------------------------------------------------------------------------
# TASK 27 — video thumbnail generation, cloud-storage-safe + async.
#
# Moved here (as a Celery task, enqueued from signals.py's
# `queue_video_thumbnail_on_create`) instead of running inline in the
# `post_save` signal or the upload view, for two independent reasons:
#   1. ffmpeg is a slow subprocess call (real videos, real disk I/O) —
#      running it synchronously inside the request/signal cycle that
#      creates a `PostMedia` row would block `PostCreateAPIView`'s
#      response for however long ffmpeg takes.
#   2. It needs to be retryable on its own schedule (transient S3 read
#      hiccup, ffmpeg momentarily OOM-killed under load) without retrying
#      the whole post-creation request.
#
# Every failure path here is `logger.exception`/`logger.error`, not
# `print()` — `print()` output only ever reaches whatever happens to be
# tailing stdout at that exact moment; `logger.error`/`.exception` is
# what Sentry's `LoggingIntegration` (settings.py) actually captures as
# an event, which is the whole point of task 27's "silently fails in
# production" complaint.
#
# ⚠️ ADJUST if your `PostMedia` model (models.py) doesn't already have a
# nullable `thumbnail` ImageField/FileField — this task assumes one
# exists to write into. Field name used below: `thumbnail`.
# ---------------------------------------------------------------------------
@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def generate_video_thumbnail(self, media_id):
    """Generate and save a JPEG thumbnail for a video `PostMedia` row.

    Storage-backend agnostic: downloads the source video to a local temp
    file via `services.download_storage_file_to_temp` (works the same
    whether `default_storage` is local disk or S3 — see task 26/
    settings.py's `USE_S3_STORAGE`), runs ffmpeg against that local copy,
    then saves the resulting thumbnail back through the model field so it
    lands in whichever storage backend is currently active — no
    S3-specific code needed here at all, exactly because it never touches
    `.path` directly.
    """
    from .models import PostMedia
    from .services import download_storage_file_to_temp, generate_video_thumbnail_file

    try:
        media = PostMedia.objects.get(id=media_id)
    except PostMedia.DoesNotExist:
        # Media row (or its parent Post) was deleted between enqueue and
        # run — nothing to do, and not an error worth retrying.
        logger.warning("generate_video_thumbnail: PostMedia %s no longer exists", media_id)
        return

    if media.media_type != "video":
        return
    if getattr(media, "thumbnail", None):
        # Already has one — avoids redoing work if this task is ever
        # retried or the signal somehow fires twice for the same row.
        return
    if not media.file:
        logger.warning("generate_video_thumbnail: PostMedia %s has no file", media_id)
        return

    _, ext = os.path.splitext(media.file.name)
    local_video_path = None
    thumb_path = None
    try:
        local_video_path = download_storage_file_to_temp(media.file, suffix=ext or ".mp4")
        thumb_path = generate_video_thumbnail_file(local_video_path)
        if thumb_path is None:
            # Already logged inside generate_video_thumbnail_file (missing
            # ffmpeg binary, ffmpeg failure, or timeout) — nothing more to
            # do here. Not retried: a video that ffmpeg can't decode won't
            # decode any better on retry #2.
            return

        with open(thumb_path, "rb") as f:
            media.thumbnail.save(f"{media.id}_thumb.jpg", File(f), save=True)
        logger.info("generate_video_thumbnail: thumbnail generated for PostMedia %s", media_id)
    except Exception as exc:
        # Genuinely unexpected failure (e.g. a transient storage-read
        # error downloading the source video) — worth a bounded retry
        # via Celery's own backoff, unlike the ffmpeg-level failures
        # above which are handled (and intentionally not retried) inside
        # generate_video_thumbnail_file.
        logger.exception("generate_video_thumbnail: failed for PostMedia %s", media_id)
        raise self.retry(exc=exc)
    finally:
        for path in (local_video_path, thumb_path):
            if path and os.path.exists(path):
                os.unlink(path)


# ---------------------------------------------------------------------------
# TASK 3 — "new post from someone you follow" fan-out.
#
# Enqueued (never called inline) from
# signals.py::queue_new_post_notification_fanout — same "don't block the
# request" reasoning as generate_video_thumbnail above, but for a much
# more common trigger (every post, not just video posts): a popular
# account's follower list can run into the thousands, and a synchronous
# loop inside PostCreateAPIView's request/response cycle would make
# every single post-create slow in direct proportion to follower count.
# This task does the actual Follow-table read and the notification
# fan-out off the request path.
#
# `post` app already imports `user_profile.Follow` directly elsewhere
# (the feed query — same precedent this reuses), so that import is at
# normal top-of-function level here too, not behind a try/except the way
# `core` is guarded in services.py — `user_profile` is a required app,
# not an optional one.
# ---------------------------------------------------------------------------
@shared_task
def notify_followers_new_post(post_id):
    """Notify every ACCEPTED follower of `post.user` that a new post went
    up. MVP version per the design doc: no per-follower "bell" opt-in
    yet — every accepted follower gets notified on every post. That's a
    deliberate, documented noise trade-off for a later pass.

    Uses `core.services.create_bulk_notifications` (one bulk INSERT)
    rather than looping `create_notification` once per follower — the
    latter would also mean one `is_restricted_between` query per
    follower for the actor-restrict check, which doesn't scale to a
    fan-out that can be thousands of rows. The restrict exclusion below
    does the same thing `create_notification` would have per-recipient,
    but as a single bulk query up front instead.
    """
    from core.models import Notification
    from core.services import create_bulk_notifications
    from user_profile.models import Follow, RestrictUser

    from .models import Post

    try:
        post = Post.objects.select_related("user").get(id=post_id)
    except Post.DoesNotExist:
        # Post (or its author) was deleted/removed between enqueue and
        # run — nothing left to notify about.
        logger.warning("notify_followers_new_post: Post %s no longer exists", post_id)
        return

    follower_ids = set(
        Follow.objects.filter(
            following_id=post.user_id, status=Follow.Status.ACCEPTED
        ).values_list("follower_id", flat=True)
    )
    if not follower_ids:
        return

    # Restrict is defined to be invisible to the restricted user (see
    # create_notification's own docstring in core/services.py) — that
    # must hold here too, not just on the single-recipient path. One
    # bulk query for every follower who has restricted this post's
    # author, instead of one is_restricted_between() call per follower.
    restricting_follower_ids = set(
        RestrictUser.objects.filter(
            user_id__in=follower_ids, restricted_id=post.user_id
        ).values_list("user_id", flat=True)
    )
    recipient_ids = follower_ids - restricting_follower_ids
    if not recipient_ids:
        return

    actor_name = post.user.get_full_name() or post.user.username
    create_bulk_notifications(
        recipient_ids,
        Notification.NotifType.NEW_POST_FROM_FOLLOWED,
        f"{actor_name} shared a new post",
        (post.content or "")[:200],
        data={"post_id": str(post.id), "actor_id": str(post.user_id)},
    )
    logger.info(
        "notify_followers_new_post: notified %d follower(s) for post %s",
        len(recipient_ids), post_id,
    )
```

Interconnections this file owns:
- **Celery** (`shared_task`, `CELERY_BEAT_SCHEDULE`) — `expire_old_stories`
  and `hard_delete_ancient_stories` are scheduled tasks; confirm the beat
  schedule snippet in this file's docstring is actually present in
  `settings.py`, or they simply never run.
- **`post.services.download_storage_file_to_temp` /
  `generate_video_thumbnail_file`** — `generate_video_thumbnail` (the
  task) is pure orchestration; both actual pieces of work (storage
  download, ffmpeg invocation) live in `services.py` (§10.1).
- **`PostMedia.thumbnail`** — ⚠️ this task assumes a nullable
  `thumbnail` ImageField/FileField already exists on `PostMedia`
  (models.py, §3) — confirm the field name matches if this ever gets
  renamed there.
- **`notify_followers_new_post`** (TASK 3, new) — reads
  `user_profile.models.Follow`/`RestrictUser` directly (top-level lazy
  import inside the function, not try/except-guarded — `user_profile`
  is a required app, same reasoning `views.py`'s own top-level `Follow`
  import already uses, §2), then calls
  `core.services.create_bulk_notifications()` with
  `Notification.NotifType.NEW_POST_FROM_FOLLOWED` — a **new,
  unconfirmed** enum member; nothing else in this doc references it
  (contrast `POST_LIKED`/`POST_COMMENTED`, §10.1, both confirmed real).
  See §22.

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
If any uploaded media is a video, `queue_video_thumbnail_on_create`
(§10.2 signal) fires automatically in the background of the same
request (via `post_save` on `PostMedia`) — enqueues
`generate_video_thumbnail` (Celery), which extracts a frame via ffmpeg.

**(TASK 3, new)** Independently, `queue_new_post_notification_fanout`
(§10.2 signal, `post_save` on `Post` itself) enqueues
`notify_followers_new_post` (§10.3) via `transaction.on_commit(...)` —
notifies every `ACCEPTED` follower of the post's author once the
create transaction commits. Off the request path, same as the
thumbnail fan-out above; see §22 for the full writeup.

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

1. ✅ **RESOLVED** — `Post.views_count` used to not be deduped even though
   `PostView` was (§13.3): `PostView.objects.get_or_create(post=instance,
   user=request.user)` only created one *view record* per user, but the
   very next line (`Post.objects.filter(id=instance.id).update(views_count=F('views_count') + 1)`)
   incremented the counter **unconditionally on every request**, including
   repeat visits by the same user. Fixed in `PostDetailAPIView.retrieve()` —
   the counter now only increments when `get_or_create`'s `created` flag is
   `True` (see the `FIX (post_app.md §14 issue #1)` comment at that call
   site), giving real "unique viewers" semantics.
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
4. ✅ **RESOLVED (B-5)** — `models.py`'s own `update_reaction_counts` (a
   second signal handler recomputing `Post.likes_count`/per-type counts on
   every `PostLike` save/delete, redundant with `signals.py`'s
   `sync_post_reaction_counts_on_save`/`_on_delete`) has been deleted from
   `models.py`. `signals.py`'s version — including the 5+-`wrong`
   auto-flag check, now folded into it — is the sole receiver. See §3
   Model notes and §19.2.
5. ✅ **RESOLVED (TASK 27)** — `auto_generate_video_thumbnail` used to
   require local filesystem storage (`instance.file.path`) and only
   `print()`d failures instead of logging them. It's been removed from
   `models.py` entirely and replaced by `signals.py`'s
   `queue_video_thumbnail_on_create` (enqueue-only) →
   `tasks.generate_video_thumbnail` (Celery) →
   `services.download_storage_file_to_temp`/`generate_video_thumbnail_file`
   (storage-API based, works on local disk **and** S3/GCS; every failure
   path goes through `logger`, not `print()`). Full writeup: §19.1.
6. ✅ **RESOLVED (TASK 25)** — `serve_media_with_range` used to be gated
   on bare `DEBUG=True`. It's now gated on its own
   `settings.SERVE_MEDIA_VIA_DJANGO` flag instead, so ops can tell at a
   glance when the fallback route is live and a prod box that hasn't wired
   up nginx yet 404s loudly instead of silently working through this
   route. You still need real media serving (nginx, S3, CDN) in
   production — this only changed the flag it's gated on, not removed the
   underlying local-dev-only nature of the view itself. See §8.
   Additionally add a new item here worth tracking: **`ChunkedUpload` and
   `CommentLike` are now both registered in `admin.py`** — an earlier
   version of this doc's §9 said they weren't; that's since been fixed
   too (see §9).
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
10. ✅ **RESOLVED** — `is_comments_disabled` used to only be enforced in
    `CommentCreateAPIView` (regular upload path); the **chunked upload
    path** (`chunked_upload_init`) didn't check it before accepting a
    video comment. Fixed: `chunked_upload_init` now resolves the target
    `Post` (via `post_id` or the parent comment's post) and 403s with
    `"Comments disabled"` up front — see the `FIX (post_app.md §14 issue
    #10)` comment at that call site — so a blocked upload fails at
    `init` time instead of after the client has already pushed chunks.
11. **`NotifType.NEW_POST_FROM_FOLLOWED` unconfirmed (NEW, TASK 3)** —
    `tasks.py::notify_followers_new_post()` references this enum member
    directly, with no fallback/shim (unlike, say, the old
    `_NotifTypeGap` pattern this codebase has used elsewhere while
    waiting on a `core` enum to land). If `core` hasn't added it, the
    task fails with `AttributeError` on every run — asynchronously,
    after `PostCreateAPIView.post()` has already returned 201, so a
    missing enum member is silent from the API caller's point of view.
    See §22.
12. **No unfollow/opt-out check on the new-post notification (NEW,
    TASK 3)** — every `ACCEPTED` follower gets notified on every post,
    same documented MVP trade-off as the home feed (§13.2) and the
    reaction/comment notifications — no per-follower "bell" opt-in
    exists yet anywhere in this app.

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
- [ ] `core` app installed & migrated with `Notification.NotifType.
      NEW_POST_FROM_FOLLOWED` defined (TASK 3, new — see §14 issue #11,
      §22) and `core.services.create_bulk_notifications()` available —
      otherwise `notify_followers_new_post` fails with `AttributeError`
      on every post-create (silently, since it runs async).
- [ ] Celery worker running — `notify_followers_new_post` (TASK 3, like
      `generate_video_thumbnail`) only ever executes if something is
      consuming the queue; without a worker it just enqueues and never
      runs, same caveat §2 already gives for video thumbnails.

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

---

## 19. Addendum 4 — services.py notification wiring reconciled (TASK 11), PostLike duplicate-signal issue (since resolved — see §20)

Several earlier sections (§3's Model notes, §16.5's "still unwired",
§17.1's "still unwired") point forward to "§19 Addendum 4" for two
things that were promised but never actually written up: the real,
final state of the notification hookup, and the still-open duplicate
`PostLike` signal. This section is that write-up, against the latest
uploaded `Services.py` (renamed `services.py` — same case-sensitivity
reasoning as §16.1/§17).

### 19.1 `services.py` — TASK 11: wired, single-notification path (batching dropped), unguarded-import crash fixed

§16.5 and §17.1 both describe `services.py` as **still unwired**, with
`notify_post_liked` routed through
`core.notification_batching.create_batched_notification` (to avoid one
notification per like in a burst) and `notify_post_commented` through
`core.services.create_notification`, using placeholder `NotifType`
constants pending `core`'s real enum. The latest uploaded `Services.py`
has moved past all three of those points, labeled in its own docstring
as "TASK 11 FIX":

- **Both** `notify_post_liked` and `notify_post_commented` now call
  **`core.services.create_notification`** — the batching approach for
  likes was dropped in favor of the simpler single-notification path.
  (If burst-deduping likes still matters, that's a follow-up, not
  something the current file attempts.)
- They use real `_Notification.NotifType.POST_LIKED` /
  `.POST_COMMENTED` values (not the placeholder constants §17.1
  described) — implying `core`'s `NotifType` enum has since picked up
  both values. Not independently re-verified against a real
  `core/models.py` in this thread; if `core`'s enum still lacks these,
  both calls will raise `AttributeError` on `_Notification.NotifType`,
  not silently no-op.
- **Now actually wired**: `notify_post_liked` is called from
  `PostReactionAPIView.post()` (only the `status_msg == "liked"`
  branch — confirmed present in the current `views.py`, matching §16.5's
  original plan), and `notify_post_commented` from both
  `CommentCreateAPIView.post()` and the chunked-upload completion view
  in `comment_view.py` (only for new top-level comments, i.e.
  `parent is None` — a large video comment finished via chunked upload
  notifies the post owner exactly the same way a regular comment does).
  §16.5/§17.1's "still unwired" is now stale — this supersedes it.
- **New in this version, not documented anywhere before now — a real
  production-crash fix**: `notify_post_liked`/`notify_post_commented`
  used to do `from core.models import Notification` as an *unguarded*
  local import inside each function. The module-level `try/except
  ImportError` around `_create_notification_row` (the thing §16.5/§17.1
  both point to as "the soft dependency") never covered this second,
  separate import — so if `core` genuinely wasn't installed in some
  environment, that unguarded import raised `ImportError` straight out
  of **every single like and every top-level comment**, i.e. crashed the
  two hottest write paths in this app. That's a worse outcome than the
  "log + no-op" the module docstring promises. Fixed the same way
  `_create_notification_row` already is: `from core.models import
  Notification as _Notification` is now wrapped in its own
  `try/except ImportError` at module scope, falling back to
  `_Notification = None`; both notify functions now check `if
  _Notification is None: return` before touching it.
- `share_post_to_conversation()` — functionally unchanged from
  §16.5/§17.1 (`post.user`/`post.content`,
  `PostShare.objects.get_or_create`, `message.services.send_message`
  still a best-guess import, still no `/share/` route in `urls.py`).

Net effect: `services.py`'s "Kept, fixed field names, still unwired"
bullet in §16.5 and all of §17.1's `create_batched_notification`
framing describe a state this app has since moved past. Treat this
section (§19.1) as the current source of truth for `services.py`;
§16.5/§17.1 remain useful history for *why* the module path/signature
were wrong in the first place, but not for what the file does today.

### 19.2 `models.py` / `signals.py` — the duplicate `PostLike` signal — ✅ RESOLVED as of B-5 (see §20)

Cross-referenced from §3's Model notes and §14 issue #4. **This was
confirmed present** in an earlier uploaded `models.py` (history kept
below for context) and **is now fixed** in the current file set — see
§20 for the write-up of what changed. Original finding, for the record:

`signals.py`'s TASK 23 docstring claimed to have *replaced*
`update_likes_count` / `update_reaction_counts` with a single
consolidated `sync_post_reaction_counts_on_save`/`_on_delete` pair — but
at the time, only `update_likes_count` had actually been deleted.
`update_reaction_counts` was still defined and still
`@receiver`-registered on `PostLike`'s `post_save`/`post_delete`
alongside `signals.py`'s newer pair, so both fired on every
like/unlike/reaction-change:

- Not a correctness bug — both recomputed the same aggregate from the
  same `PostLike` rows and landed on identical numbers.
- It **was** real duplicated work (two full aggregate-recompute + UPDATE
  passes instead of one) on the hottest write path in this app, and it
  directly contradicted what the TASK 23 docstring said was done.
- The two were **not** fully interchangeable: `models.py`'s
  `update_reaction_counts` also auto-set `moderation_status='flagged'`
  once a post accumulated 5+ `wrong` reactions — a lightweight
  community-moderation signal that `signals.py`'s
  `sync_post_reaction_counts` did not replicate.

**Fix applied (B-5), as recommended here:** the 5+-`wrong` auto-flag
check was moved into `signals.py`'s `sync_post_reaction_counts` (using
the per-type counts already in hand from its own aggregate query — no
extra query needed), and `update_reaction_counts` plus its now-unused
`from django.db.models import Count` / receiver imports were deleted
from `models.py` entirely. See §3's code block and Model notes, §14
issue #4, and §20 for the current state.

### 19.3 TASK 27 (video thumbnails) — no new information, consolidated pointer only

§3's Model notes and the inline "TASK 27" comment blocks across
`models.py`, `signals.py`, `services.py`, and `tasks.py` already fully
document this move (sync ffmpeg-in-signal → async Celery task,
`.path`-based file access → storage-API-based, `print()` → `logger`).
Nothing in the latest uploaded files changes that account; §14 issue #5
above now points here for the resolved status instead of restating it.

---

## 20. Addendum 5 — this pass: B-5 finally resolved, G-3 (RestrictUser) new feature, doc-sync fixes

Two files actually changed logic in this pass: `models.py` (B-5) and
`serializers.py` (G-3). Everything else (`views.py`, `comment_view.py`,
`comment_serializers.py`, `admin.py`, `apps.py`, `tests.py`) is
byte-for-byte what this doc already described — the remaining items
below are this doc catching up to sections that had drifted out of sync
with files that *hadn't* changed (`urls.py`'s §8 code block, `admin.py`'s
trailing note), not new code.

1. **B-5 — `PostLike` duplicate-signal issue is resolved.** `models.py`'s
   `update_reaction_counts` (the second receiver on `PostLike`'s
   `post_save`/`post_delete`, redundant with `signals.py`'s
   `sync_post_reaction_counts`) has been deleted, along with its
   now-unused `Count` import. Its 5+-`wrong` auto-flag-to-`flagged`
   check was folded into `signals.py`'s `sync_post_reaction_counts`
   instead of being dropped. This is exactly the "recommended fix, not
   yet applied" that §19.2 used to describe — now applied. Updated: §3
   code block + Model notes, §14 issue #4, §19.2.
2. **G-3 — new feature: restrict-aware comment previews.** `serializers.
   py`'s `PostDetailSerializer.get_comments()` now excludes comments from
   users the **post owner** has restricted (`user_profile.RestrictUser`),
   applied before the `[:10]` slice so restricted comments don't crowd
   out visible ones. A restricted user still sees their own comments
   when viewing the post themselves. This is the first place outside
   `user_profile` itself that actually consumes restrict data — see §4's
   code block and Serializer notes, and §2's external-dependencies note
   (now mentions `RestrictUser` alongside `Follow`). No test coverage
   yet for this path — `tests.py`'s class list (§11) is unchanged from
   §17.5/§18.3.
3. **Doc-sync fix — `urls.py` §8 code block was stale.** The actual file
   has used `settings.SERVE_MEDIA_VIA_DJANGO` (TASK 25) for a while —
   this doc's §2, §14 issue #6, and the endpoint-table area already said
   so — but the §8 code block itself still showed the older bare
   `if settings.DEBUG:` gate. Code block and its "Notes" paragraph are
   now updated to match the real file. No actual code changed here; only
   this document did.
4. **Doc-sync fix — `admin.py`'s trailing note was self-contradicting.**
   The §9 code block already showed `ChunkedUploadAdmin` and
   `CommentLikeAdmin` registered, but a leftover note right below it
   still claimed those two models "are not registered in admin." Note
   corrected to match the code directly above it (and to also mention
   `Story`/`StoryView`, which are newly registered too).

No migration-shape changes in this pass — B-5 and the doc-sync items
touch signal wiring and documentation only; G-3 adds no new field or
model, just a cross-app read in a serializer method.
---

## 21. Addendum 6 — full doc-vs-code verification pass (Sep 2026 sync)

This pass did a **programmatic, byte-for-byte diff** of every "full
code" section in this doc against the actual current source files
(same method used on `login_app_reference.md`) — not a re-read, an
actual line-by-line comparison — so nothing was eyeballed and missed.

**Files diffed and confirmed exact-match, no changes needed:**
`models.py` (§3), `serializers.py` (§4), `views.py` (§6),
`comment_view.py` (§7), `urls.py` (§8), `admin.py` (§9), `apps.py`
(§10).

**One real bug found and fixed:**

- **§5 `comment_serializers.py`** — the code fence contained the
  **entire dead, commented-out draft** (the old `PostCommentSerializer`
  without comment-reactions) pasted directly ahead of the real active
  code, inside the *same* fence. The prose note directly above it
  claimed "only the active code below is included" (correctly true for
  §4's `serializers.py`, right next to it) but was never actually true
  for this section — an editing slip when this section was first
  written. Net effect: anyone copy-pasting "the full file" from §5 got
  ~120 lines of dead code prepended to the real thing. Fixed: the dead
  draft is removed from the code fence entirely, the missing
  `# post/comment_serializers.py` header line was restored, and the
  note above it now accurately describes both the original draft/active
  split *and* this doc-level fix, so a future reader isn't confused by
  a note that doesn't match what's below it.

**Three files promoted from "scattered addenda excerpts" to full,
canonical, verified sections:**

- **§10.1 `services.py`**, **§10.2 `signals.py`**, **§10.3 `tasks.py`**
  — previously these three lived only as partial quotes and rationale
  spread across §16, §17.1, §19, and this file's own module docstrings;
  there was no one place with the complete, current, copy-pasteable
  code the way every other file in this app already had. All three are
  now included in full, verified byte-for-byte against the actual
  uploaded (post-rename, lowercase-filename) source, with their
  cross-app interconnections (`core.services.create_notification`,
  `core.models.Notification`, `message.services.send_message` —
  unwired/best-guess, `login`'s `User.posts_count`, Celery/
  `CELERY_BEAT_SCHEDULE`) called out directly under each section instead
  of only inside prose elsewhere. The existing addenda (§16, §17.1, §19)
  are left in place as-is — they're the historical record of *why* each
  fix happened — §10.1–§10.3 are the current, standalone reference for
  *what the code actually is right now*.

**Not changed, deliberately:**

- `tests.py` (§11) stays as a descriptive list, not a full-code section
  — same treatment as `login_app_reference.md` gives its own
  (admittedly much shorter) `tests.py`. Its current content
  (`PostCreateTests`, `PostDeleteTests`, `ReactionIdempotencyTests`,
  `CommentThreadingTests`, `SavePostTests`, `HashtagDiscoveryTests`,
  `ExploreFeedTests`, `StoryExpiryTests`) was spot-checked against the
  actual `tests.py` on disk and is still accurate — no new test classes
  need adding to this list.
- `__init__.py` (§12) — still genuinely empty, nothing to sync.

**Going forward:** this file is the single source of truth for the
`post` app, same as `login_app_reference.md` is for `login`. Any future
code change to `models.py`, `serializers.py`, `comment_serializers.py`,
`views.py`, `comment_view.py`, `services.py`, `signals.py`, `tasks.py`,
`urls.py`, or `admin.py` should be reflected in this doc's matching
section in the same turn, so the two never drift apart again.

---

## 22. Addendum 7 — TASK 3: "new post from someone you follow" fan-out (new feature)

Two files actually changed logic in this pass: `signals.py` and
`tasks.py`. Both were diffed byte-for-byte against §10.2/§10.3 above —
this is the only change; every other line in both files is unchanged
from §21's already-verified content.

1. **`signals.py` — one new receiver, `queue_new_post_notification_fanout`**
   (`post_save` on `Post`). Enqueue-only, same shape as
   `queue_video_thumbnail_on_create` further down the same file, but
   uses `transaction.on_commit(lambda: notify_followers_new_post.delay(
   instance.id))` instead of a bare `.delay()` — deliberately, because
   `Post` creation may run inside `@transaction.atomic` (several other
   views in this codebase already wrap writes that way), and a bare
   `.delay()` risks the Celery worker querying for the `Post` row
   before its transaction has actually committed. `on_commit()` also
   degrades safely to "runs immediately" when there's no open
   transaction at all. Updated: §10.2 code block + intro blurb +
   interconnections notes.
2. **`tasks.py` — one new task, `notify_followers_new_post(post_id)`.**
   Re-fetches the `Post` (handles it having been deleted between
   enqueue and run — logs and returns, no error), resolves `ACCEPTED`
   followers via `user_profile.models.Follow`, excludes anyone who has
   restricted the post's author via `user_profile.models.RestrictUser`
   (one bulk query, same shape §4's `PostDetailSerializer.get_comments()`
   restrict-exclusion already uses, and the same shape
   `testseries.tasks.notify_followers_new_testseries` uses for the
   equivalent feature in that app), then calls
   `core.services.create_bulk_notifications()` — one bulk INSERT,
   deliberately not a per-follower loop through `create_notification()`
   (`services.py`'s `_notify()` helper), which wouldn't scale to a
   follower list that can run into the thousands. Updated: §10.3 code
   block + intro blurb + interconnections notes.
3. **New, unconfirmed cross-app dependency:**
   `Notification.NotifType.NEW_POST_FROM_FOLLOWED` — referenced
   directly by `notify_followers_new_post`, with no fallback the way
   this codebase's older `_NotifTypeGap`-style shims used to provide
   while waiting on a `core` enum to land. Not independently verified
   against `core`'s actual enum list this pass (`core`'s source wasn't
   part of this upload). Added as **§14 issue #11** and a new checklist
   line in **§15**. Unlike `POST_LIKED`/`POST_COMMENTED` (§10.1, both
   confirmed real), this one should be treated as **open** until
   checked.
4. **No test coverage added for this feature** — `tests.py`'s class
   list (§11) is unchanged from §17.5/§18.3/§21; same "no coverage yet"
   treatment G-3 (restrict-aware comment previews, §20) got when it
   landed.
5. **No migration-shape change** — `signals.py`/`tasks.py` are pure
   Python (signal wiring + a Celery task); no model field was added or
   changed in this pass.

---