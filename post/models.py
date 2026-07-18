import uuid
from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import FileExtensionValidator
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.conf import settings
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


# class PostComment(models.Model):
#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='comments')
#     user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='post_comments')
#     parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='replies')
#
#     content = models.TextField(blank=True)
#     likes_count = models.PositiveIntegerField(default=0)
#     replies_count = models.PositiveIntegerField(default=0)
#
#     is_edited = models.BooleanField(default=False)
#     is_deleted = models.BooleanField(default=False)
#     is_pinned = models.BooleanField(default=False)
#
#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)
#     deleted_at = models.DateTimeField(blank=True, null=True)
#
#     class Meta:
#         db_table = 'post_comments'
#         ordering = ['-created_at']
#         indexes = [
#             models.Index(fields=['post', '-created_at']),
#             models.Index(fields=['parent', '-created_at']),
#         ]
#
#     def __str__(self):
#         return f"{self.user} - {self.content[:30]}"
#
#
# class CommentMedia(models.Model):
#     MEDIA_TYPES = (
#         ('image', 'Image'),
#         ('video', 'Video'),
#         ('document', 'Document'),
#     )
#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     comment = models.ForeignKey(PostComment, on_delete=models.CASCADE, related_name='media')
#     media_type = models.CharField(max_length=20, choices=MEDIA_TYPES)
#     file = models.FileField(upload_to='comment_media/%Y/%m/%d/')
#     file_name = models.CharField(max_length=255, blank=True)
#     file_size = models.PositiveIntegerField(default=0)  # bytes
#
#     created_at = models.DateTimeField(auto_now_add=True)
#
#     class Meta:
#         db_table = 'comment_media'
#
#     def save(self, *args, **kwargs):
#         if self.file and not self.file_name:
#             self.file_name = self.file.name
#         super().save(*args, **kwargs)


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


@receiver(post_save, sender=PostLike)
@receiver(post_delete, sender=PostLike)
def update_likes_count(sender, instance, **kwargs):
    Post.objects.filter(id=instance.post_id).update(
        likes_count=PostLike.objects.filter(post_id=instance.post_id).count()
    )

@receiver(post_save, sender=PostComment)
@receiver(post_delete, sender=PostComment)
def update_comments_count(sender, instance, **kwargs):
    Post.objects.filter(id=instance.post_id).update(
        comments_count=PostComment.objects.filter(post_id=instance.post_id, is_deleted=False).count()
    )
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
        try:
            video_input_path = instance.file.path
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
            print("FFmpeg Error stdout:", e.stdout.decode('utf8') if e.stdout else "")
            print("FFmpeg Error stderr:", e.stderr.decode('utf8') if e.stderr else "")
        except Exception as e:
            print(f"Thumbnail Extraction Error: {e}")


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