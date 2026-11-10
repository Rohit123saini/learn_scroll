# HLS rendition (240p / 360p / 540p / 720p): planning, ffmpeg command, storage upload, task glue, API field.
# ffmpeg itself is NOT run here (see _attach_hls tests: transcode is mocked).
import os
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.storage import FileSystemStorage
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIRequestFactory

from . import services as svc
from .models import Post, PostMedia

User = get_user_model()


class PlanLadderTests(SimpleTestCase):
    def names(self, w, h):
        return [r["name"] for r in svc.plan_hls_ladder(w, h)]

    def test_full_hd_portrait_gets_all_four_rungs(self):
        self.assertEqual(self.names(1080, 1920), ["240p", "360p", "540p", "720p"])

    def test_never_upscales(self):
        self.assertEqual(self.names(540, 960), ["240p", "360p", "540p"])
        self.assertEqual(self.names(720, 1280), ["240p", "360p", "540p", "720p"])

    def test_tiny_source_still_gets_one_rung(self):
        self.assertEqual(self.names(240, 426), ["240p"])
        self.assertEqual(self.names(200, 356), ["240p"])  # below every rung: the lowest one anyway

    @override_settings(VIDEO_HLS_LITE_RUNG=False)
    def test_lite_rung_can_be_switched_off(self):
        self.assertEqual(self.names(1080, 1920), ["360p", "540p", "720p"])
        self.assertEqual(self.names(240, 426), ["360p"])  # old behaviour: tiny source gets 360p

    def test_landscape_uses_the_short_side(self):
        r = svc.plan_hls_ladder(1920, 1080)
        self.assertEqual([x["name"] for x in r], ["240p", "360p", "540p", "720p"])
        self.assertFalse(r[0]["portrait"])
        self.assertTrue(svc.plan_hls_ladder(1080, 1920)[0]["portrait"])

    def test_unknown_size_gets_the_full_ladder(self):
        self.assertEqual(self.names(None, None), ["240p", "360p", "540p", "720p"])


class CommandTests(SimpleTestCase):
    def cmd(self, rungs, audio=True):
        return svc.build_hls_command("/in.mp4", "/out", rungs, has_audio=audio)

    def test_four_rungs_with_audio(self):
        rungs = svc.plan_hls_ladder(1080, 1920)
        c = self.cmd(rungs)
        joined = " ".join(c)
        self.assertIn("split=4", joined)
        self.assertEqual(c.count("-map"), 8)  # 4 video + 4 audio
        self.assertEqual(
            c[c.index("-var_stream_map") + 1],
            "v:0,a:0,name:240p v:1,a:1,name:360p v:2,a:2,name:540p v:3,a:3,name:720p",
        )
        self.assertEqual(c[c.index("-master_pl_name") + 1], "master.m3u8")
        self.assertEqual(c[c.index("-hls_playlist_type") + 1], "vod")
        self.assertIn("-b:v:3", c)
        self.assertEqual(c[-1], os.path.join("/out", "%v", "index.m3u8"))
        # portrait: width is the short side
        self.assertIn("scale=w='min(240,iw)':h=-2", joined)
        self.assertIn("scale=w='min(360,iw)':h=-2", joined)

    def test_landscape_scales_by_height(self):
        c = self.cmd(svc.plan_hls_ladder(1920, 1080))
        self.assertIn("scale=w=-2:h='min(720,ih)'", " ".join(c))

    def test_single_rung_has_no_split(self):
        c = self.cmd(svc.plan_hls_ladder(240, 426))
        self.assertNotIn("split", " ".join(c))

    def test_no_audio_maps_no_audio(self):
        c = self.cmd(svc.plan_hls_ladder(1080, 1920), audio=False)
        self.assertEqual(c.count("-map"), 4)
        self.assertNotIn("-c:a", c)
        self.assertEqual(c[c.index("-var_stream_map") + 1], "v:0,name:240p v:1,name:360p v:2,name:540p v:3,name:720p")

    def test_keyframes_follow_the_segment_grid(self):
        c = self.cmd(svc.plan_hls_ladder(1080, 1920))
        self.assertEqual(c[c.index("-force_key_frames") + 1], "expr:gte(t,n_forced*4)")


class OutputTests(SimpleTestCase):
    def make(self, root, rungs, ended=True, segments=True, master=True):
        if master:
            open(os.path.join(root, "master.m3u8"), "w").write("#EXTM3U\n")
        for r in rungs:
            d = os.path.join(root, r["name"])
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "index.m3u8"), "w").write("#EXTM3U\n" + ("#EXT-X-ENDLIST\n" if ended else ""))
            if segments:
                open(os.path.join(d, "seg_0000.ts"), "wb").write(b"x")

    def test_verify(self):
        rungs = svc.plan_hls_ladder(1080, 1920)
        for kwargs, ok in (({}, True), ({"ended": False}, False), ({"segments": False}, False), ({"master": False}, False)):
            with tempfile.TemporaryDirectory() as d:
                self.make(d, rungs, **kwargs)
                self.assertEqual(svc.verify_hls_output(d, rungs), ok, kwargs)

    def test_upload_puts_master_last_and_returns_its_name(self):
        rungs = svc.plan_hls_ladder(540, 960)
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as media:
            self.make(src, rungs)
            storage = FileSystemStorage(location=media, base_url="/media/")
            order = []
            real_save = storage.save
            storage.save = lambda name, f: (order.append(name), real_save(name, f))[1]
            master = svc.upload_hls_directory(storage, src, "posts/hls/abc/run1")
            self.assertEqual(master, "posts/hls/abc/run1/master.m3u8")
            self.assertEqual(order[-1], "posts/hls/abc/run1/master.m3u8")
            self.assertTrue(storage.exists("posts/hls/abc/run1/360p/seg_0000.ts"))
            self.assertEqual(svc.delete_storage_tree(storage, "posts/hls/abc/run1"), 7)  # master + 3 rungs x (playlist + segment)
            self.assertFalse(storage.exists("posts/hls/abc/run1/master.m3u8"))

    def test_failed_upload_cleans_up(self):
        rungs = svc.plan_hls_ladder(540, 960)
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as media:
            self.make(src, rungs)
            storage = FileSystemStorage(location=media)
            real = storage.save
            calls = {"n": 0}

            def flaky(name, f):
                calls["n"] += 1
                if calls["n"] == 3:
                    raise OSError("disk full")
                return real(name, f)

            storage.save = flaky
            with self.assertRaises(OSError):
                svc.upload_hls_directory(storage, src, "posts/hls/abc/run2")
            self.assertEqual(svc.delete_storage_tree(storage, "posts/hls/abc/run2"), 0)

    def test_prefix_of(self):
        self.assertEqual(svc.hls_prefix_of("posts/hls/a/b/master.m3u8"), "posts/hls/a/b")
        self.assertEqual(svc.hls_prefix_of("posts/x.mp4"), "")
        self.assertEqual(svc.hls_prefix_of(""), "")


class TaskAndApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", password="x")
        self.post = Post.objects.create(user=self.user, content="c", post_type="video", category="tech", visibility="public")
        self.media = PostMedia.objects.create(
            post=self.post, media_type="video", file="posts/x.mp4", file_name="x.mp4", file_size_bytes=1,
            mime_type="video/mp4", duration_seconds=20, width=1080, height=1920)

    def _run(self, **kw):
        from . import tasks
        built = {"dir": tempfile.mkdtemp(), "rungs": svc.plan_hls_ladder(1080, 1920)}
        with mock.patch.object(svc, "_run_ffprobe", return_value={"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "20"}}), \
             mock.patch.object(svc, "transcode_video_hls", return_value=built), \
             mock.patch.object(svc, "upload_hls_directory", return_value="posts/hls/m/r1/master.m3u8") as up:
            return tasks._attach_hls(str(self.media.pk), "/tmp/x.mp4", **kw), up

    def test_saves_master_playlist_and_keeps_status(self):
        ok, _ = self._run()
        self.assertTrue(ok)
        self.media.refresh_from_db()
        self.assertEqual(self.media.hls_playlist, "posts/hls/m/r1/master.m3u8")
        self.assertEqual(self.media.processing_status, PostMedia.PROCESSING_READY)
        self.assertEqual(self.media.metadata["hls"]["rungs"], ["240p", "360p", "540p", "720p"])

    def test_skips_when_already_built_unless_forced(self):
        PostMedia.objects.filter(pk=self.media.pk).update(hls_playlist="posts/hls/m/old/master.m3u8")
        ok, up = self._run()
        self.assertFalse(ok)
        up.assert_not_called()
        ok, up = self._run(force=True)
        self.assertTrue(ok)

    @override_settings(VIDEO_HLS_ENABLED=False)
    def test_disabled(self):
        ok, up = self._run()
        self.assertFalse(ok)
        up.assert_not_called()

    @override_settings(VIDEO_HLS_MAX_SECONDS=10)
    def test_too_long_gets_mp4_only(self):
        ok, up = self._run()
        self.assertFalse(ok)
        up.assert_not_called()

    def test_ffmpeg_failure_is_swallowed_and_status_untouched(self):
        from . import tasks
        with mock.patch.object(svc, "_run_ffprobe", return_value={"streams": [{"codec_type": "video", "width": 1080, "height": 1920}], "format": {"duration": "20"}}), \
             mock.patch.object(svc, "transcode_video_hls", return_value=None):
            self.assertFalse(tasks._attach_hls(str(self.media.pk), "/tmp/x.mp4"))
        self.media.refresh_from_db()
        self.assertEqual(self.media.hls_playlist, "")
        self.assertEqual(self.media.processing_status, PostMedia.PROCESSING_READY)

    def test_api_exposes_hls_url_and_null_without_it(self):
        from .serializers import hls_playlist_url, PostMediaSerializer
        req = APIRequestFactory().get("/")
        self.assertIsNone(hls_playlist_url(req, self.media))
        PostMedia.objects.filter(pk=self.media.pk).update(hls_playlist="posts/hls/m/r1/master.m3u8")
        self.media.refresh_from_db()
        url = hls_playlist_url(req, self.media)
        self.assertTrue(url.endswith("posts/hls/m/r1/master.m3u8"), url)
        from rest_framework.request import Request
        data = PostMediaSerializer(self.media, context={"request": Request(req)}).data
        self.assertEqual(data["hls_url"], url)
        self.assertTrue(data["file"].endswith("posts/x.mp4"))  # MP4 fallback still there

    # ---- P-E2: lowest rung ("quick start") url
    def test_low_url_points_at_the_lowest_rung_next_to_the_master(self):
        from .serializers import hls_low_url
        req = APIRequestFactory().get("/")
        self.assertIsNone(hls_low_url(req, self.media))  # no HLS yet
        PostMedia.objects.filter(pk=self.media.pk).update(
            hls_playlist="posts/hls/m/r1/master.m3u8", metadata={"hls": {"rungs": ["240p", "360p", "540p"]}})
        self.media.refresh_from_db()
        self.assertTrue(hls_low_url(req, self.media).endswith("posts/hls/m/r1/240p/index.m3u8"))

    def test_low_url_is_null_for_an_old_row_that_does_not_know_its_rungs(self):
        from .serializers import hls_low_url
        PostMedia.objects.filter(pk=self.media.pk).update(hls_playlist="posts/hls/m/r1/master.m3u8", metadata={})
        self.media.refresh_from_db()
        self.assertIsNone(hls_low_url(APIRequestFactory().get("/"), self.media))

    def test_lowest_rung_name_rejects_path_tricks(self):
        class M:  # noqa: D401 - tiny stand-in
            metadata = {"hls": {"rungs": ["../x", "360p"]}}
        self.assertIsNone(svc.lowest_hls_rung_name(M()))
        M.metadata = {"hls": {"rungs": []}}
        self.assertIsNone(svc.lowest_hls_rung_name(M()))

