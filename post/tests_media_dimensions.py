"""
post/tests_media_dimensions.py — TASK 1.1-BE tests (guaranteed media size/duration).

Run:  python manage.py test post.tests_media_dimensions

Pure parsing tests need nothing but Pillow. The DB tests use a throw-away
MEDIA_ROOT (FileSystemStorage) and mock `post.services._run_ffprobe`, so no
ffprobe binary is required.
"""
import io
import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image

from .models import Post, PostMedia
from .services import (
    media_metadata_missing,
    parse_ffprobe_output,
    probe_image_size,
)
from .tasks import probe_media_metadata

TMP_MEDIA = tempfile.mkdtemp(prefix="t11be_media_")


def _jpeg(width=640, height=360, orientation=None):
    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), (10, 120, 200))
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        img.save(buf, "JPEG", exif=exif)
    else:
        img.save(buf, "JPEG")
    return buf.getvalue()


def _ffprobe_video(width=1920, height=1080, duration="12.4", rotate=None, side_rotation=None):
    stream = {"codec_type": "video", "width": width, "height": height, "duration": duration}
    if rotate is not None:
        stream["tags"] = {"rotate": str(rotate)}
    if side_rotation is not None:
        stream["side_data_list"] = [{"side_data_type": "Display Matrix", "rotation": side_rotation}]
    return {"streams": [stream], "format": {"duration": duration}}


class ParseFfprobeTests(SimpleTestCase):
    def test_landscape_video(self):
        out = parse_ffprobe_output(_ffprobe_video(), "video")
        self.assertEqual(out, {"width": 1920, "height": 1080, "duration_seconds": 12})

    def test_rotate_tag_swaps_to_displayed_size(self):
        out = parse_ffprobe_output(_ffprobe_video(1920, 1080, rotate=90), "video")
        self.assertEqual((out["width"], out["height"]), (1080, 1920))

    def test_side_data_negative_rotation_swaps(self):
        out = parse_ffprobe_output(_ffprobe_video(1920, 1080, side_rotation=-90), "video")
        self.assertEqual((out["width"], out["height"]), (1080, 1920))

    def test_180_rotation_does_not_swap(self):
        out = parse_ffprobe_output(_ffprobe_video(1920, 1080, rotate=180), "video")
        self.assertEqual((out["width"], out["height"]), (1920, 1080))

    def test_sub_second_duration_floors_at_one(self):
        out = parse_ffprobe_output(_ffprobe_video(duration="0.2"), "video")
        self.assertEqual(out["duration_seconds"], 1)

    def test_zero_or_missing_duration_is_unknown(self):
        self.assertNotIn("duration_seconds", parse_ffprobe_output(_ffprobe_video(duration="0"), "video"))
        data = {"streams": [{"codec_type": "video", "width": 10, "height": 10}], "format": {}}
        self.assertNotIn("duration_seconds", parse_ffprobe_output(data, "video"))

    def test_audio_gets_duration_only_even_with_cover_art(self):
        data = {
            "streams": [
                {"codec_type": "audio"},
                {"codec_type": "video", "width": 500, "height": 500, "disposition": {"attached_pic": 1}},
            ],
            "format": {"duration": "183.7"},
        }
        self.assertEqual(parse_ffprobe_output(data, "audio"), {"duration_seconds": 184})

    def test_attached_pic_is_not_a_video_frame(self):
        data = {
            "streams": [{"codec_type": "video", "width": 500, "height": 500, "disposition": {"attached_pic": 1}}],
            "format": {"duration": "5"},
        }
        self.assertNotIn("width", parse_ffprobe_output(data, "video"))

    def test_garbage_input(self):
        self.assertEqual(parse_ffprobe_output({}, "video"), {})
        self.assertEqual(parse_ffprobe_output({"streams": [{"codec_type": "video", "width": "x"}]}, "video"), {})


class ProbeImageTests(SimpleTestCase):
    def test_plain_jpeg(self):
        self.assertEqual(probe_image_size(io.BytesIO(_jpeg(800, 400))), (800, 400))

    def test_exif_rotated_photo_reports_displayed_size(self):
        self.assertEqual(probe_image_size(io.BytesIO(_jpeg(800, 400, orientation=6))), (400, 800))

    def test_corrupt_file_raises(self):
        with self.assertRaises(Exception):
            probe_image_size(io.BytesIO(b"not an image"))


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class MediaDimensionsDbTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="t11be_user", password="pw12345!", email="t11be@example.com"
        )
        for target in ("post.tasks.notify_followers_new_post.delay",
                       "post.tasks.generate_image_variants.delay",
                       "post.tasks.generate_video_thumbnail.delay"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.post = Post.objects.create(user=self.user, post_type="image", content="t11be")

    def make_media(self, media_type="image", data=None, name="a.jpg", **extra):
        data = data if data is not None else _jpeg()
        return PostMedia.objects.create(
            post=self.post, media_type=media_type,
            file=SimpleUploadedFile(name, data, content_type="application/octet-stream"),
            file_name=name, file_size_bytes=len(data), mime_type="application/octet-stream", **extra,
        )

    def blank(self, media):
        PostMedia.objects.filter(pk=media.pk).update(width=None, height=None, duration_seconds=None)
        media.refresh_from_db()
        return media

    # ---- upload-time guarantee -------------------------------------------
    def test_image_has_dimensions_immediately_after_create(self):
        with mock.patch("post.tasks.probe_media_metadata.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                media = self.make_media(data=_jpeg(640, 360))
        self.assertEqual((media.width, media.height), (640, 360))
        media.refresh_from_db()
        self.assertEqual((media.width, media.height), (640, 360))
        delay.assert_not_called()  # nothing left to do -> no task queued

    def test_video_gets_size_and_duration_at_create(self):
        with mock.patch("post.services._run_ffprobe", return_value=_ffprobe_video(1920, 1080, duration="12.4")):
            with mock.patch("post.tasks.probe_media_metadata.delay") as delay:
                with self.captureOnCommitCallbacks(execute=True):
                    media = self.make_media("video", data=b"fake-video", name="v.mp4")
        media.refresh_from_db()
        self.assertEqual((media.width, media.height, media.duration_seconds), (1920, 1080, 12))
        delay.assert_not_called()

    def test_client_supplied_values_are_never_overwritten(self):
        media = self.make_media(data=_jpeg(640, 360), width=111, height=222)
        media.refresh_from_db()
        self.assertEqual((media.width, media.height), (111, 222))

    def test_failed_probe_queues_task_after_commit_and_never_breaks_upload(self):
        with mock.patch("post.services._run_ffprobe", return_value=None):
            with mock.patch("post.tasks.probe_media_metadata.delay") as delay:
                with self.captureOnCommitCallbacks(execute=True):
                    media = self.make_media("video", data=b"broken", name="v.mp4")
        self.assertTrue(PostMedia.objects.filter(pk=media.pk).exists())
        delay.assert_called_once_with(str(media.id))

    def test_broker_outage_does_not_break_upload(self):
        with mock.patch("post.services._run_ffprobe", return_value=None):
            with mock.patch("post.tasks.probe_media_metadata.delay", side_effect=ConnectionError("down")):
                with self.captureOnCommitCallbacks(execute=True):
                    media = self.make_media("video", data=b"broken", name="v.mp4")
        self.assertTrue(PostMedia.objects.filter(pk=media.pk).exists())

    def test_documents_are_ignored(self):
        with mock.patch("post.tasks.probe_media_metadata.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                media = self.make_media("document", data=b"%PDF-1.4", name="d.pdf")
        delay.assert_not_called()
        self.assertFalse(media_metadata_missing(media))

    # ---- task -------------------------------------------------------------
    def test_task_fills_blank_row(self):
        media = self.blank(self.make_media(data=_jpeg(300, 500)))
        self.assertTrue(probe_media_metadata.apply(args=[str(media.id)]).get())
        media.refresh_from_db()
        self.assertEqual((media.width, media.height), (300, 500))

    def test_task_on_missing_row_is_a_noop(self):
        self.assertFalse(probe_media_metadata.apply(args=["00000000-0000-0000-0000-000000000000"]).get())

    # ---- backfill command ---------------------------------------------------
    def test_backfill_dry_run_counts_only_blank_rows(self):
        blank_img = self.blank(self.make_media(data=_jpeg()))
        self.make_media(data=_jpeg(), name="ok.jpg")  # already complete
        self.make_media("document", data=b"%PDF", name="d.pdf")
        out = io.StringIO()
        call_command("backfill_media_dimensions", "--dry-run", stdout=out)
        self.assertIn("1 media row(s)", out.getvalue())
        blank_img.refresh_from_db()
        self.assertIsNone(blank_img.width)  # dry run changed nothing

    def test_backfill_sync_fills_image_and_video(self):
        img = self.blank(self.make_media(data=_jpeg(1000, 500)))
        with mock.patch("post.services._run_ffprobe", return_value=_ffprobe_video(1080, 1920, duration="30")):
            vid = self.blank(self.make_media("video", data=b"v", name="v.mp4"))
            call_command("backfill_media_dimensions", "--sync", stdout=io.StringIO())
        img.refresh_from_db(); vid.refresh_from_db()
        self.assertEqual((img.width, img.height), (1000, 500))
        self.assertEqual((vid.width, vid.height, vid.duration_seconds), (1080, 1920, 30))

    def test_backfill_treats_zero_as_blank_and_is_idempotent(self):
        media = self.make_media(data=_jpeg(640, 360))
        PostMedia.objects.filter(pk=media.pk).update(width=0, height=0)
        call_command("backfill_media_dimensions", "--sync", stdout=io.StringIO())
        media.refresh_from_db()
        self.assertEqual((media.width, media.height), (640, 360))
        out = io.StringIO()
        call_command("backfill_media_dimensions", "--dry-run", stdout=out)
        self.assertIn("0 media row(s)", out.getvalue())

    def test_backfill_unprobeable_file_is_skipped_not_fatal(self):
        bad = self.blank(self.make_media(data=b"not an image", name="bad.jpg"))
        out = io.StringIO()
        call_command("backfill_media_dimensions", "--sync", stdout=out)
        self.assertIn("1 skipped", out.getvalue())
        bad.refresh_from_db()
        self.assertIsNone(bad.width)
