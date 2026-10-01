"""
post/test_image_variants.py — C4-BE tests (image sizes + BlurHash).

Run:  python manage.py test post.test_image_variants

Covers the "done when" list: a new upload gets 3 sizes (original + 320 + 720),
the backfill command fills old rows, and the serializer stays backward
compatible. Files go to a throw-away MEDIA_ROOT (FileSystemStorage); if your
test settings force S3 (USE_S3_STORAGE), override STORAGES here too.
"""
import io
import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIRequestFactory

from .models import Post, PostMedia
from .serializers import PostMediaSerializer
from .tasks import generate_image_variants

TMP_MEDIA = tempfile.mkdtemp(prefix="c4be_media_")


def _make_user():
    # Adjust if your custom User needs other required fields.
    return get_user_model().objects.create_user(username="c4be_user", password="pw12345!", email="c4be@example.com")


def _jpeg(width=2000, height=1000, color=(10, 120, 200)):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "JPEG")
    return buf.getvalue()


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class ImageVariantsTestBase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.user = _make_user()
        # Post creation may fan out notifications through Celery in your signals — keep that off the broker.
        patcher = mock.patch("post.tasks.notify_followers_new_post.delay")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.post = Post.objects.create(user=self.user, post_type="image", content="c4be")

    def make_media(self, media_type="image", data=None, name="a.jpg"):
        """Create a PostMedia. The post_save receiver would enqueue the task, so
        by default `.delay` is mocked and nothing runs until a test calls the task."""
        data = data if data is not None else _jpeg()
        upload = SimpleUploadedFile(name, data, content_type="image/jpeg")
        with mock.patch("post.tasks.generate_image_variants.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                media = PostMedia.objects.create(
                    post=self.post, media_type=media_type, file=upload,
                    file_name=name, file_size_bytes=len(data), mime_type="image/jpeg",
                )
        media.delay_mock = delay
        return media


class TriggerTests(ImageVariantsTestBase):
    def test_new_image_enqueues_task_once_after_commit(self):
        media = self.make_media("image")
        media.delay_mock.assert_called_once_with(str(media.id))

    def test_not_enqueued_before_commit(self):
        with mock.patch("post.tasks.generate_image_variants.delay") as delay:
            PostMedia.objects.create(
                post=self.post, media_type="image", file=SimpleUploadedFile("b.jpg", _jpeg(), "image/jpeg"),
                file_name="b.jpg", file_size_bytes=1, mime_type="image/jpeg",
            )
            delay.assert_not_called()  # inside the (test) transaction, commit hasn't happened

    def test_video_and_document_not_enqueued(self):
        self.assertFalse(self.make_media("video", name="v.mp4").delay_mock.called)
        self.assertFalse(self.make_media("document", name="d.pdf").delay_mock.called)

    def test_updating_existing_row_does_not_requeue(self):
        media = self.make_media("image")
        with mock.patch("post.tasks.generate_image_variants.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                media.caption = "changed"
                media.save()
            delay.assert_not_called()

    def test_broker_failure_does_not_break_upload(self):
        with mock.patch("post.tasks.generate_image_variants.delay", side_effect=ConnectionError("broker down")):
            with self.captureOnCommitCallbacks(execute=True):
                media = PostMedia.objects.create(
                    post=self.post, media_type="image", file=SimpleUploadedFile("c.jpg", _jpeg(), "image/jpeg"),
                    file_name="c.jpg", file_size_bytes=1, mime_type="image/jpeg",
                )
        self.assertTrue(PostMedia.objects.filter(pk=media.pk).exists())


class TaskTests(ImageVariantsTestBase):
    def test_generates_three_sizes_blurhash_and_dimensions(self):
        media = self.make_media()
        self.assertTrue(generate_image_variants.apply(args=[str(media.id)]).get())
        media.refresh_from_db()

        self.assertTrue(media.file and media.thumb_320 and media.medium_720)  # original + 2 variants = 3 sizes
        with Image.open(media.thumb_320.path) as t, Image.open(media.medium_720.path) as m, Image.open(media.file.path) as o:
            self.assertEqual(t.size, (320, 160))
            self.assertEqual(m.size, (720, 360))
            self.assertEqual(o.size, (2000, 1000))  # original untouched
        self.assertEqual(len(media.blur_hash), 28)
        self.assertEqual((media.width, media.height), (2000, 1000))

    def test_is_idempotent(self):
        media = self.make_media()
        generate_image_variants.apply(args=[str(media.id)]).get()
        media.refresh_from_db()
        first = media.thumb_320.name
        self.assertFalse(generate_image_variants.apply(args=[str(media.id)]).get())  # skipped
        media.refresh_from_db()
        self.assertEqual(media.thumb_320.name, first)

    def test_force_regenerates_and_removes_old_files(self):
        media = self.make_media()
        generate_image_variants.apply(args=[str(media.id)]).get()
        media.refresh_from_db()
        old = media.thumb_320.path
        self.assertTrue(generate_image_variants.apply(args=[str(media.id)], kwargs={"force": True}).get())
        media.refresh_from_db()
        self.assertNotEqual(media.thumb_320.path, old)
        with self.assertRaises(FileNotFoundError):
            open(old, "rb")

    def test_does_not_overwrite_existing_dimensions(self):
        media = self.make_media()
        PostMedia.objects.filter(pk=media.pk).update(width=111, height=222)
        generate_image_variants.apply(args=[str(media.id)]).get()
        media.refresh_from_db()
        self.assertEqual((media.width, media.height), (111, 222))

    def test_corrupt_image_is_skipped_without_retry(self):
        media = self.make_media(data=b"definitely not an image")
        self.assertFalse(generate_image_variants.apply(args=[str(media.id)]).get())
        media.refresh_from_db()
        self.assertFalse(media.thumb_320)
        self.assertFalse(media.blur_hash)

    def test_video_row_is_ignored(self):
        media = self.make_media("video", name="v.mp4")
        self.assertFalse(generate_image_variants.apply(args=[str(media.id)]).get())

    def test_animated_gif_gets_thumb_but_no_medium(self):
        frames = [Image.new("RGB", (600, 400), c).convert("P") for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
        buf = io.BytesIO()
        frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
        media = self.make_media("gif", data=buf.getvalue(), name="a.gif")
        generate_image_variants.apply(args=[str(media.id)]).get()
        media.refresh_from_db()
        self.assertTrue(media.thumb_320)
        self.assertFalse(media.medium_720)  # the animation keeps being served from `file`
        self.assertTrue(media.blur_hash)


class SerializerTests(ImageVariantsTestBase):
    def serialize(self, media):
        request = APIRequestFactory().get("/")
        return PostMediaSerializer(media, context={"request": request}).data

    def test_old_fields_still_present(self):
        data = self.serialize(self.make_media())
        for key in ("id", "media_type", "file", "thumbnail", "file_name", "file_size_bytes",
                    "mime_type", "width", "height", "duration_seconds", "display_order", "caption"):
            self.assertIn(key, data)

    def test_unprocessed_image_falls_back_to_original(self):
        media = self.make_media()
        data = self.serialize(media)
        self.assertTrue(data["thumb_url"] and data["thumb_url"] == data["file"])
        self.assertTrue(data["medium_url"] and data["medium_url"] == data["file"])
        self.assertIsNone(data["blurhash"])

    def test_processed_image_returns_variants(self):
        media = self.make_media()
        generate_image_variants.apply(args=[str(media.id)]).get()
        media.refresh_from_db()
        data = self.serialize(media)
        self.assertIn("_320", data["thumb_url"])
        self.assertIn("_720", data["medium_url"])
        self.assertTrue(data["thumb_url"].startswith("http"))  # absolute, like `file`
        self.assertEqual(len(data["blurhash"]), 28)

    def test_document_has_no_image_urls(self):
        data = self.serialize(self.make_media("document", name="d.pdf"))
        self.assertIsNone(data["thumb_url"])
        self.assertIsNone(data["medium_url"])
        self.assertIsNone(data["blurhash"])


class BackfillCommandTests(ImageVariantsTestBase):
    def call(self, *args, **kwargs):
        out = io.StringIO()
        call_command("backfill_image_variants", *args, stdout=out, stderr=io.StringIO(), **kwargs)
        return out.getvalue()

    def setUp(self):
        super().setUp()
        self.old1 = self.make_media(name="o1.jpg")
        self.old2 = self.make_media(name="o2.jpg")
        self.video = self.make_media("video", name="v.mp4")

    def test_dry_run_changes_nothing(self):
        with mock.patch("post.tasks.generate_image_variants.delay") as delay:
            out = self.call("--dry-run")
        delay.assert_not_called()
        self.assertIn("2 image row(s) match", out)

    def test_async_queues_only_images_missing_variants(self):
        generate_image_variants.apply(args=[str(self.old1.id)]).get()  # old1 already done
        with mock.patch("post.tasks.generate_image_variants.delay") as delay:
            self.call("--batch-size", "1")
        delay.assert_called_once_with(str(self.old2.id), force=False)

    def test_sync_fills_old_rows(self):
        self.call("--sync")
        for media in (self.old1, self.old2):
            media.refresh_from_db()
            self.assertTrue(media.thumb_320 and media.medium_720 and media.blur_hash)
        self.video.refresh_from_db()
        self.assertFalse(self.video.thumb_320)
        # Second run has nothing left to do.
        self.assertIn("Nothing to do", self.call("--sync"))

    def test_limit_and_force(self):
        self.call("--sync")
        with mock.patch("post.tasks.generate_image_variants.delay") as delay:
            self.call("--force", "--limit", "1")
        self.assertEqual(delay.call_count, 1)
        self.assertEqual(delay.call_args.kwargs, {"force": True})

    def test_bad_arguments(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            self.call("--batch-size", "0")
        with self.assertRaises(CommandError):
            self.call("--before", "not-a-date")
