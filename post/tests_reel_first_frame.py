# P-E2 - reels first frame: BlurHash for VIDEO posters (task + backfill), quick-start rung url in the reel payload.
# Written, not run (no test runner here). Pure PIL; ffmpeg is never started.
import os
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase

from . import services as svc
from .models import Post, PostMedia

User = get_user_model()


def _jpeg_file(directory, color=(200, 60, 40), size=(90, 160)):
    from PIL import Image

    path = os.path.join(directory, "poster.jpg")
    Image.new("RGB", size, color).save(path, "JPEG")
    return path


class BlurhashForFileTests(SimpleTestCase):
    def test_poster_file_gives_a_28_char_blurhash(self):
        with tempfile.TemporaryDirectory() as d:
            value = svc.blurhash_for_file(_jpeg_file(d))
        self.assertEqual(len(value), 28)  # 3x4 components (portrait) = same length as the image BlurHash

    def test_unreadable_file_gives_none_and_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "x.jpg")
            open(bad, "wb").write(b"not an image")
            self.assertIsNone(svc.blurhash_for_file(bad))
        self.assertIsNone(svc.blurhash_for_file("/definitely/not/here.jpg"))


class VideoPosterBlurhashTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="v", password="x")
        self.post = Post.objects.create(user=self.user, content="c", post_type="video", category="tech", visibility="public")
        self.media = PostMedia.objects.create(
            post=self.post, media_type="video", file="posts/x.mp4", file_name="x.mp4", file_size_bytes=1,
            mime_type="video/mp4", duration_seconds=5, width=720, height=1280)

    def test_new_poster_also_gets_a_blurhash(self):
        from . import tasks

        with tempfile.TemporaryDirectory() as d:
            poster = _jpeg_file(d)
            with mock.patch.object(svc, "download_storage_file_to_temp", return_value=poster), \
                 mock.patch.object(svc, "generate_video_thumbnail_file", return_value=poster):
                # the task deletes its temp paths, so hand it copies
                with mock.patch("os.unlink"):
                    tasks.generate_video_thumbnail.apply(args=[str(self.media.pk)]).get()
        self.media.refresh_from_db()
        self.assertTrue(self.media.thumbnail)
        self.assertEqual(len(self.media.blur_hash), 28)

    def test_old_row_with_poster_but_no_blurhash_is_healed_without_ffmpeg(self):
        from . import tasks

        self.media.thumbnail.save("old_thumb.jpg", ContentFile(open(self._poster(), "rb").read()), save=True)
        self.assertFalse(self.media.blur_hash)
        with mock.patch.object(svc, "generate_video_thumbnail_file") as ff:
            tasks.generate_video_thumbnail.apply(args=[str(self.media.pk)]).get()
        ff.assert_not_called()  # no new poster is made
        self.media.refresh_from_db()
        self.assertEqual(len(self.media.blur_hash), 28)

    def test_existing_blurhash_is_left_alone(self):
        from . import tasks

        self.media.thumbnail.save("old_thumb.jpg", ContentFile(open(self._poster(), "rb").read()), save=True)
        PostMedia.objects.filter(pk=self.media.pk).update(blur_hash="LEHV6nWB2yk8pyo0adR*.7kCMdnj")
        tasks.generate_video_thumbnail.apply(args=[str(self.media.pk)]).get()
        self.media.refresh_from_db()
        self.assertEqual(self.media.blur_hash, "LEHV6nWB2yk8pyo0adR*.7kCMdnj")

    def _poster(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return _jpeg_file(d)
