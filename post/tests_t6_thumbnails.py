# T6 - poster-frame fallbacks + self-healing. Written by reading the code; NOT run.
#   python manage.py test post.tests_t6_thumbnails
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase

from . import services


class ThumbnailFallbackTests(SimpleTestCase):
    def test_tries_next_offset_when_first_gives_nothing(self):
        calls = []

        def fake(path, offset, timeout):
            calls.append(offset)
            return "/tmp/x.jpg" if offset == "00:00:00.5" else None

        with mock.patch.object(services.shutil, "which", return_value="/usr/bin/ffmpeg"), \
                mock.patch.object(services, "_thumbnail_at", side_effect=fake):
            self.assertEqual(services.generate_video_thumbnail_file("/v.mp4"), "/tmp/x.jpg")
        self.assertEqual(calls, ["00:00:01", "00:00:00.5"])

    def test_all_offsets_fail_returns_none(self):
        with mock.patch.object(services.shutil, "which", return_value="/usr/bin/ffmpeg"), \
                mock.patch.object(services, "_thumbnail_at", return_value=None) as m:
            self.assertIsNone(services.generate_video_thumbnail_file("/v.mp4"))
        self.assertEqual(m.call_count, 3)

    def test_no_ffmpeg_returns_none_without_attempts(self):
        with mock.patch.object(services.shutil, "which", return_value=None), \
                mock.patch.object(services, "_thumbnail_at") as m:
            self.assertIsNone(services.generate_video_thumbnail_file("/v.mp4"))
        m.assert_not_called()


class EnsureThumbnailTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def _media(self, **kw):
        base = dict(pk="abc", media_type="video", thumbnail=None, file="posts/v.mp4")
        base.update(kw)
        return mock.Mock(**base)

    def test_queues_once_per_window(self):
        with mock.patch("post.tasks.generate_video_thumbnail") as task:
            self.assertTrue(services.ensure_video_thumbnail(self._media()))
            self.assertFalse(services.ensure_video_thumbnail(self._media()))
        task.delay.assert_called_once_with("abc")

    def test_skips_images_and_rows_with_thumbnail(self):
        with mock.patch("post.tasks.generate_video_thumbnail") as task:
            self.assertFalse(services.ensure_video_thumbnail(self._media(media_type="image")))
            self.assertFalse(services.ensure_video_thumbnail(self._media(thumbnail="t.jpg")))
        task.delay.assert_not_called()

    def test_broker_down_never_raises(self):
        with mock.patch("post.tasks.generate_video_thumbnail") as task:
            task.delay.side_effect = RuntimeError("broker down")
            self.assertFalse(services.ensure_video_thumbnail(self._media()))
