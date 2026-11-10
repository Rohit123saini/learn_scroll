# Reel composer cover: POST /post/create/ with `reel_cover` stores it as the video thumbnail.
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from .models import Post, PostMedia
from .services import attach_reel_cover

User = get_user_model()
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


@override_settings(CELERY_TASK_ALWAYS_EAGER=False)
class ReelCoverTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.post = Post.objects.create(user=self.me, content="c", post_type="video", category="tech", visibility="public")
        self.media = PostMedia.objects.create(
            post=self.post, media_type="video", file="posts/x.mp4", file_name="x.mp4", file_size_bytes=1, mime_type="video/mp4")

    def test_valid_cover_becomes_thumbnail(self):
        f = SimpleUploadedFile("c.jpg", JPEG, content_type="image/jpeg")
        self.assertTrue(attach_reel_cover(self.post, f))
        self.media.refresh_from_db()
        self.assertTrue(self.media.thumbnail)

    def test_wrong_type_or_empty_is_ignored(self):
        self.assertFalse(attach_reel_cover(self.post, SimpleUploadedFile("c.txt", b"x", content_type="text/plain")))
        self.assertFalse(attach_reel_cover(self.post, SimpleUploadedFile("c.jpg", b"", content_type="image/jpeg")))
        self.media.refresh_from_db()
        self.assertFalse(self.media.thumbnail)

    def test_post_without_video_is_ignored(self):
        self.media.delete()
        self.assertFalse(attach_reel_cover(self.post, SimpleUploadedFile("c.jpg", JPEG, content_type="image/jpeg")))
