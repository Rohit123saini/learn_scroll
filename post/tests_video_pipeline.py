"""
post/tests_video_pipeline.py — V1 tests ("feed me video nahi chal rahi").

Run:  python manage.py test post.tests_video_pipeline

No ffmpeg/ffprobe binary and no DB needed: planning/box-walking are pure, and the
ffmpeg wrappers run with `subprocess.run` / `shutil.which` mocked.
"""
import os
import struct
import tempfile
from unittest import mock

from django.test import SimpleTestCase

from . import services as svc


def _info(vcodec="h264", pix_fmt="yuv420p", width=1280, height=720, acodec="aac", attached_pic=False):
    streams = [{
        "codec_type": "video", "codec_name": vcodec, "pix_fmt": pix_fmt,
        "width": width, "height": height,
        "disposition": {"attached_pic": 1 if attached_pic else 0},
    }]
    if acodec:
        streams.append({"codec_type": "audio", "codec_name": acodec})
    return {"streams": streams}


def _box(kind, payload=b""):
    return struct.pack(">I", 8 + len(payload)) + kind + payload


class PlanVideoProcessingTests(SimpleTestCase):
    def test_h264_aac_mp4_faststart_is_left_alone(self):
        plan = svc.plan_video_processing(_info(), ext=".mp4", moov_first=True)
        self.assertEqual(plan["action"], "none")
        self.assertEqual(plan["reasons"], [])

    def test_unknown_moov_position_on_good_mp4_is_left_alone(self):
        self.assertEqual(svc.plan_video_processing(_info(), ext="mp4", moov_first=None)["action"], "none")

    def test_no_audio_track_is_fine(self):
        self.assertEqual(svc.plan_video_processing(_info(acodec=None), ext="mp4", moov_first=True)["action"], "none")

    def test_hevc_is_transcoded(self):
        plan = svc.plan_video_processing(_info(vcodec="hevc"), ext=".mp4", moov_first=True)
        self.assertEqual(plan["action"], "transcode")
        self.assertTrue(any("hevc" in r for r in plan["reasons"]))
        self.assertFalse(plan["audio_copy"])

    def test_10_bit_h264_is_transcoded(self):
        plan = svc.plan_video_processing(_info(pix_fmt="yuv420p10le"), ext=".mp4", moov_first=True)
        self.assertEqual(plan["action"], "transcode")

    def test_4k_h264_is_transcoded(self):
        plan = svc.plan_video_processing(_info(width=3840, height=2160), ext=".mp4", moov_first=True)
        self.assertEqual(plan["action"], "transcode")

    def test_portrait_4k_is_transcoded(self):
        plan = svc.plan_video_processing(_info(width=2160, height=3840), ext=".mp4", moov_first=True)
        self.assertEqual(plan["action"], "transcode")

    def test_moov_at_end_is_remuxed_with_audio_copy(self):
        plan = svc.plan_video_processing(_info(), ext=".mp4", moov_first=False)
        self.assertEqual(plan["action"], "remux")
        self.assertTrue(plan["audio_copy"])
        self.assertTrue(any("moov" in r for r in plan["reasons"]))

    def test_mov_container_is_remuxed(self):
        plan = svc.plan_video_processing(_info(), ext=".mov", moov_first=True)
        self.assertEqual(plan["action"], "remux")

    def test_odd_audio_codec_is_remuxed_without_audio_copy(self):
        plan = svc.plan_video_processing(_info(acodec="opus"), ext=".mp4", moov_first=True)
        self.assertEqual(plan["action"], "remux")
        self.assertFalse(plan["audio_copy"])

    def test_no_video_stream_is_skipped(self):
        self.assertEqual(svc.plan_video_processing({"streams": [{"codec_type": "audio", "codec_name": "aac"}]})["action"], "skip")
        self.assertEqual(svc.plan_video_processing({})["action"], "skip")

    def test_cover_art_only_is_not_a_video(self):
        plan = svc.plan_video_processing(_info(attached_pic=True), ext=".mp4", moov_first=True)
        self.assertEqual(plan["action"], "skip")


class Mp4MoovPositionTests(SimpleTestCase):
    def _write(self, data):
        fd, path = tempfile.mkstemp(suffix=".mp4")
        os.write(fd, data)
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return path

    def test_moov_before_mdat(self):
        path = self._write(_box(b"ftyp", b"isom0000") + _box(b"moov", b"x" * 20) + _box(b"mdat", b"y" * 100))
        self.assertIs(svc.mp4_moov_before_mdat(path), True)

    def test_moov_after_mdat(self):
        path = self._write(_box(b"ftyp", b"isom0000") + _box(b"mdat", b"y" * 100) + _box(b"moov", b"x" * 20))
        self.assertIs(svc.mp4_moov_before_mdat(path), False)

    def test_largesize_mdat_before_moov(self):
        big = struct.pack(">I", 1) + b"mdat" + struct.pack(">Q", 16 + 50) + b"z" * 50
        path = self._write(_box(b"ftyp", b"isom0000") + big + _box(b"moov", b"x"))
        self.assertIs(svc.mp4_moov_before_mdat(path), False)

    def test_garbage_and_empty_are_unknown(self):
        self.assertIsNone(svc.mp4_moov_before_mdat(self._write(b"")))
        self.assertIsNone(svc.mp4_moov_before_mdat(self._write(b"abc")))
        self.assertIsNone(svc.mp4_moov_before_mdat(self._write(struct.pack(">I", 2) + b"free" + b"\x00" * 8)))

    def test_missing_file_is_unknown(self):
        self.assertIsNone(svc.mp4_moov_before_mdat("/nonexistent/definitely/missing.mp4"))


class FfmpegWrapperTests(SimpleTestCase):
    """ffmpeg is mocked: the fake `run` writes the output file like ffmpeg would."""

    def _fake_run(self, rc=0, write=True):
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            if write:
                with open(cmd[-1], "wb") as fh:
                    fh.write(b"fake-mp4")
            return mock.Mock(returncode=rc, stderr=b"boom")

        return run, calls

    def test_transcode_builds_h264_aac_faststart_command(self):
        run, calls = self._fake_run()
        with mock.patch("post.services.shutil.which", return_value="/usr/bin/ffmpeg"), \
                mock.patch("post.services.subprocess.run", side_effect=run):
            out = svc.transcode_video_h264("/tmp/in.mov")
        self.addCleanup(lambda: out and os.path.exists(out) and os.unlink(out))
        self.assertIsNotNone(out)
        cmd = calls[0]
        self.assertIn("libx264", cmd)
        self.assertIn("aac", cmd)
        self.assertEqual(cmd[cmd.index("-movflags") + 1], "+faststart")
        self.assertEqual(cmd[cmd.index("-pix_fmt") + 1], "yuv420p")
        self.assertIn("0:a:0?", cmd)  # silent videos must not fail
        self.assertTrue(any("scale=" in part for part in cmd))

    def test_remux_copies_video_and_audio(self):
        run, calls = self._fake_run()
        with mock.patch("post.services.shutil.which", return_value="/usr/bin/ffmpeg"), \
                mock.patch("post.services.subprocess.run", side_effect=run):
            out = svc.remux_video_faststart("/tmp/in.mp4", audio_copy=True)
        self.addCleanup(lambda: out and os.path.exists(out) and os.unlink(out))
        cmd = calls[0]
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "copy")
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "copy")
        self.assertEqual(cmd[cmd.index("-movflags") + 1], "+faststart")

    def test_remux_reencodes_odd_audio_to_aac(self):
        run, calls = self._fake_run()
        with mock.patch("post.services.shutil.which", return_value="/usr/bin/ffmpeg"), \
                mock.patch("post.services.subprocess.run", side_effect=run):
            out = svc.remux_video_faststart("/tmp/in.mp4", audio_copy=False)
        self.addCleanup(lambda: out and os.path.exists(out) and os.unlink(out))
        cmd = calls[0]
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "aac")

    def test_ffmpeg_failure_returns_none_and_cleans_up(self):
        run, calls = self._fake_run(rc=1, write=True)
        with mock.patch("post.services.shutil.which", return_value="/usr/bin/ffmpeg"), \
                mock.patch("post.services.subprocess.run", side_effect=run):
            self.assertIsNone(svc.transcode_video_h264("/tmp/in.mov"))
        self.assertFalse(os.path.exists(calls[0][-1]))  # partial output removed

    def test_missing_ffmpeg_returns_none(self):
        with mock.patch("post.services.shutil.which", return_value=None):
            self.assertIsNone(svc.transcode_video_h264("/tmp/in.mov"))
            self.assertIsNone(svc.remux_video_faststart("/tmp/in.mp4"))


class VerifyStreamableTests(SimpleTestCase):
    def _verify(self, info, moov_first=True, parsed=None):
        parsed = {"width": 720, "height": 1280, "duration_seconds": 5} if parsed is None else parsed
        with mock.patch("post.services._run_ffprobe", return_value=info), \
                mock.patch("post.services.mp4_moov_before_mdat", return_value=moov_first), \
                mock.patch("post.services.parse_ffprobe_output", return_value=parsed):
            return svc.verify_streamable_video("/tmp/out.mp4")

    def test_good_output_passes(self):
        self.assertEqual(self._verify(_info())["duration_seconds"], 5)

    def test_probe_failure_fails(self):
        self.assertIsNone(self._verify(None))

    def test_wrong_codec_or_pix_fmt_fails(self):
        self.assertIsNone(self._verify(_info(vcodec="hevc")))
        self.assertIsNone(self._verify(_info(pix_fmt="yuv420p10le")))

    def test_moov_at_end_fails(self):
        self.assertIsNone(self._verify(_info(), moov_first=False))

    def test_zero_duration_fails(self):
        self.assertIsNone(self._verify(_info(), parsed={"width": 1, "height": 1}))
