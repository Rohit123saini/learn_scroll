# post/tests_reel_rb.py - X12 R-B: small Reels gaps.
#   * R-B3  ReelSerializer.video.caption_tracks  (lecture auto / uploaded subtitles of the reel's video, no N+1)
#   * R-B4  feed card chip: PostListSerializer.reel_study = {series, chapters} (reel_meta.feed_study_chip)
#   * R-B6  tests that were missing for the video pipeline of reels:
#             - thumbnail sprite plan        (services.plan_video_sprite / build_sprite_command)
#             - retry endpoint               (POST /post/media/<id>/retry-processing/)
#             - stuck / failed sweeper       (tasks.retry_failed_video_processing)  + the double-enqueue regression fix
#             - failure bookkeeping + notify (tasks._mark_processing_failed / notify_video_processing_failed)
# NOT RUN when written. Run: python manage.py test post.tests_reel_rb     (no migration needed)
import datetime
import uuid
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import services, tasks
from .models import Post, PostMedia, VideoCaptionTrack
from .reel_meta import feed_study_chip
from .serializers import PostListSerializer, reel_caption_tracks_payload
from .tests_reels import ReelsBase


# ----------------------------------------------------------------------------- R-B3 caption tracks
class CaptionTracksUnitTests(SimpleTestCase):
    """reel_caption_tracks_payload reads ONLY the prefetched rows: no prefetch -> [] and no query."""

    class _Boom:
        def all(self):
            raise AssertionError("caption_tracks.all() must not be called without a prefetch")

    def _track(self, language, status="ready", name="/media/a.vtt", source="auto"):
        return SimpleNamespace(language=language, status=status, source=source, file=SimpleNamespace(url=name) if name else None)

    def test_not_prefetched_gives_empty_list_and_no_query(self):
        media = SimpleNamespace(caption_tracks=self._Boom())
        self.assertEqual(reel_caption_tracks_payload(None, media), [])

    def test_prefetched_ready_tracks_are_sorted_by_language(self):
        tracks = [self._track("hi", name="/media/hi.vtt"), self._track("en", name="/media/en.vtt", source="upload")]
        media = SimpleNamespace(
            _prefetched_objects_cache={"caption_tracks": tracks},
            caption_tracks=SimpleNamespace(all=lambda: tracks),
        )
        self.assertEqual(reel_caption_tracks_payload(None, media), [
            {"language": "en", "source": "upload", "url": "/media/en.vtt"},
            {"language": "hi", "source": "auto", "url": "/media/hi.vtt"},
        ])

    def test_not_ready_or_without_file_are_skipped(self):
        tracks = [self._track("en", status="pending"), self._track("hi", status="failed"), self._track("en", name=None)]
        media = SimpleNamespace(
            _prefetched_objects_cache={"caption_tracks": tracks},
            caption_tracks=SimpleNamespace(all=lambda: tracks),
        )
        self.assertEqual(reel_caption_tracks_payload(None, media), [])


class CaptionTracksFeedTests(ReelsBase):
    def media_of(self, post):
        return PostMedia.objects.get(post=post)

    def track(self, post, language="hi", status="ready", name="posts/captions/a.vtt", source="auto"):
        return VideoCaptionTrack.objects.create(media=self.media_of(post), language=language, status=status, file=name, source=source)

    def video_of(self, post):
        item = next(x for x in self.get().data["results"] if x["id"] == str(post.id))
        return item["video"]

    def test_reel_without_tracks_has_empty_list(self):
        p = self.reel()
        self.assertEqual(self.video_of(p)["caption_tracks"], [])

    def test_ready_track_is_listed_with_language_source_and_url(self):
        p = self.reel()
        self.track(p, "hi", source="auto")
        tracks = self.video_of(p)["caption_tracks"]
        self.assertEqual(len(tracks), 1)
        self.assertEqual((tracks[0]["language"], tracks[0]["source"]), ("hi", "auto"))
        self.assertIn("a.vtt", tracks[0]["url"])
        self.assertEqual(set(tracks[0]), {"language", "source", "url"})

    def test_pending_processing_failed_and_fileless_tracks_are_hidden(self):
        p = self.reel()
        self.track(p, "en", status="pending", name="")
        self.track(p, "hi", status="failed", name="")
        self.assertEqual(self.video_of(p)["caption_tracks"], [])
        VideoCaptionTrack.objects.filter(media=self.media_of(p), language="hi").update(status="ready")  # ready but no file
        self.assertEqual(self.video_of(p)["caption_tracks"], [])

    def test_two_languages_sorted(self):
        p = self.reel()
        self.track(p, "hi", name="posts/captions/hi.vtt")
        self.track(p, "en", name="posts/captions/en.vtt", source="upload")
        self.assertEqual([t["language"] for t in self.video_of(p)["caption_tracks"]], ["en", "hi"])

    def test_studio_captions_stay_independent(self):
        meta = {"reel": {"v": 1, "clips": 1, "duration": 20, "captions": {"language": "en", "cues": [{"s": 0, "e": 2, "t": "Hi"}]}}}
        p = self.reel(metadata=meta)
        self.track(p, "hi")
        v = self.video_of(p)
        self.assertEqual(v["captions"]["language"], "en")
        self.assertEqual(len(v["caption_tracks"]), 1)

    def test_query_count_does_not_grow_with_the_page(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count(n):
            Post.objects.filter(post_type="video").delete()
            cache.clear()
            for p in [self.reel(self.friend if i % 2 else self.stranger, likes=i) for i in range(n)]:
                self.track(p, "hi")
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(len(self.get(page_size=30).data["results"]), n)
            return len(ctx.captured_queries)

        few, many = count(3), count(15)
        self.assertLessEqual(many, few, "caption tracks caused an N+1")


# ----------------------------------------------------------------------------- R-B6 sprite plan
class SpritePlanTests(SimpleTestCase):
    KW = dict(max_frames=100, cols=10, thumb_width=160)

    def test_short_video_one_frame_per_second(self):
        self.assertEqual(
            services.plan_video_sprite(30, 1080, 1920, **self.KW),
            {"interval": 1, "frames": 30, "cols": 10, "rows": 3, "thumb_w": 160, "thumb_h": 284},
        )

    def test_long_video_interval_grows_and_frames_are_capped(self):
        plan = services.plan_video_sprite(250, 1080, 1920, **self.KW)
        self.assertEqual((plan["interval"], plan["frames"], plan["cols"], plan["rows"]), (3, 84, 10, 9))
        plan = services.plan_video_sprite(7200, 1080, 1920, **self.KW)
        self.assertEqual((plan["interval"], plan["frames"], plan["rows"]), (72, 100, 10))

    def test_never_more_than_max_frames(self):
        for dur in (1, 59, 99, 100, 101, 599, 3600, 12345):
            plan = services.plan_video_sprite(dur, 720, 1280, **self.KW)
            self.assertLessEqual(plan["frames"], 100, dur)
            self.assertGreaterEqual(plan["frames"], 1, dur)
            self.assertGreaterEqual(plan["frames"] * plan["interval"], dur - plan["interval"] + 1, dur)  # whole video is covered

    def test_columns_shrink_to_the_frame_count(self):
        plan = services.plan_video_sprite(3, 1080, 1920, **self.KW)
        self.assertEqual((plan["frames"], plan["cols"], plan["rows"]), (3, 3, 1))

    def test_sub_second_video_still_has_one_frame(self):
        plan = services.plan_video_sprite(0.5, 1080, 1920, **self.KW)
        self.assertEqual((plan["interval"], plan["frames"], plan["cols"], plan["rows"]), (1, 1, 1, 1))

    def test_unknown_or_bad_duration_gives_none(self):
        for bad in (None, 0, -5, "abc", float("nan"), ""):
            self.assertIsNone(services.plan_video_sprite(bad, 1080, 1920, **self.KW), repr(bad))

    def test_unknown_size_falls_back_to_16_by_9_portrait(self):
        plan = services.plan_video_sprite(30, None, None, **self.KW)
        self.assertEqual((plan["thumb_w"], plan["thumb_h"]), (160, 284))

    def test_tile_height_is_clamped_and_even(self):
        self.assertEqual(services.plan_video_sprite(30, 100, 10000, **self.KW)["thumb_h"], 480)  # <= 3 x width
        self.assertEqual(services.plan_video_sprite(30, 1920, 100, **self.KW)["thumb_h"], 8)
        for w, h in ((160, 161), (1, 1), (333, 777), (1080, 1921)):
            plan = services.plan_video_sprite(30, w, h, **self.KW)
            self.assertEqual(plan["thumb_h"] % 2, 0, (w, h))
            self.assertEqual(plan["thumb_w"] % 2, 0, (w, h))

    def test_thumb_width_is_made_even_and_has_a_floor(self):
        self.assertEqual(services.plan_video_sprite(30, 160, 160, max_frames=100, cols=10, thumb_width=161)["thumb_w"], 160)
        self.assertEqual(services.plan_video_sprite(30, 160, 160, max_frames=100, cols=10, thumb_width=10)["thumb_w"], 40)

    @override_settings(VIDEO_SPRITE_MAX_FRAMES=20, VIDEO_SPRITE_COLS=4, VIDEO_SPRITE_THUMB_WIDTH=100)
    def test_defaults_come_from_settings(self):
        self.assertEqual(
            services.plan_video_sprite(60, 100, 200),
            {"interval": 3, "frames": 20, "cols": 4, "rows": 5, "thumb_w": 100, "thumb_h": 200},
        )

    def test_ffmpeg_command_matches_the_plan(self):
        plan = services.plan_video_sprite(30, 1080, 1920, **self.KW)
        cmd = services.build_sprite_command("/tmp/in.mp4", "/tmp/out.jpg", plan)
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("/tmp/in.mp4", cmd)
        self.assertEqual(cmd[-1], "/tmp/out.jpg")
        self.assertIn("fps=1/1,scale=160:284,tile=10x3", cmd)
        self.assertIn("-an", cmd)  # no audio in a sprite

    def test_generate_returns_none_without_a_plan_and_without_ffmpeg(self):
        self.assertIsNone(services.generate_video_sprite_file("/tmp/in.mp4", None))
        with mock.patch.object(services.shutil, "which", return_value=None):
            self.assertIsNone(services.generate_video_sprite_file("/tmp/in.mp4", services.plan_video_sprite(30, 1080, 1920, **self.KW)))


# ----------------------------------------------------------------------------- helpers for the DB tests
class _MediaBase(ReelsBase):
    def video(self, owner=None, status=PostMedia.PROCESSING_FAILED, pipeline=None, age_minutes=None):
        """(post, media) of a reel whose video row has `status`, an optional `metadata.pipeline` and an upload age."""
        post = self.reel(owner or self.me)
        media = PostMedia.objects.get(post=post)
        fields = {"processing_status": status}
        if pipeline is not None:
            fields["metadata"] = {"pipeline": pipeline}
        if age_minutes is not None:
            fields["created_at"] = timezone.now() - datetime.timedelta(minutes=age_minutes)
        PostMedia.objects.filter(pk=media.pk).update(**fields)
        media.refresh_from_db()
        return post, media

    @staticmethod
    def status_of(media):
        return PostMedia.objects.get(pk=media.pk).processing_status

    @staticmethod
    def pipe_of(media):
        return (PostMedia.objects.get(pk=media.pk).metadata or {}).get("pipeline") or {}


# ----------------------------------------------------------------------------- R-B6 retry endpoint
class RetryEndpointTests(_MediaBase):
    def url(self, media):
        return reverse("media-retry-processing", args=[media.id])

    def post_retry(self, media):
        return self.client.post(self.url(media))

    def test_owner_retry_requeues_with_a_fresh_budget(self):
        _, media = self.video(pipeline={"attempts": 3, "notified": True, "manual_retries": 0, "last_error": "boom"})
        with mock.patch.object(tasks.process_video_for_streaming, "delay") as delay:
            r = self.post_retry(media)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data, {"media_id": str(media.id), "processing_status": "pending", "queued": True})
        delay.assert_called_once_with(str(media.id), True)
        self.assertEqual(self.status_of(media), "pending")
        pipe = self.pipe_of(media)
        self.assertEqual((pipe["attempts"], pipe["notified"], pipe["manual_retries"], pipe["last_error"]), (0, False, 1, ""))

    def test_second_tap_is_idempotent(self):
        _, media = self.video()
        with mock.patch.object(tasks.process_video_for_streaming, "delay") as delay:
            first = self.post_retry(media)
            second = self.post_retry(media)
        self.assertTrue(first.data["queued"])
        self.assertFalse(second.data["queued"])
        self.assertEqual(second.data["processing_status"], "pending")
        self.assertEqual(delay.call_count, 1)

    def test_pending_processing_and_ready_are_not_queued_again(self):
        for status in ("pending", "processing", "ready"):
            _, media = self.video(status=status)
            with mock.patch.object(tasks.process_video_for_streaming, "delay") as delay:
                r = self.post_retry(media)
            self.assertEqual(r.status_code, 200, status)
            self.assertEqual((r.data["processing_status"], r.data["queued"]), (status, False), status)
            delay.assert_not_called()

    def test_someone_elses_video_is_404(self):
        _, media = self.video(owner=self.stranger)
        with mock.patch.object(tasks.process_video_for_streaming, "delay") as delay:
            r = self.post_retry(media)
        self.assertEqual(r.status_code, 404)
        delay.assert_not_called()
        self.assertEqual(self.status_of(media), "failed")

    def test_deleted_post_is_404(self):
        post, media = self.video()
        Post.objects.filter(pk=post.pk).update(is_deleted=True)
        self.assertEqual(self.post_retry(media).status_code, 404)

    def test_unknown_media_is_404(self):
        self.assertEqual(self.client.post(reverse("media-retry-processing", args=[uuid.uuid4()])).status_code, 404)

    def test_only_videos_can_be_retried(self):
        post = self.reel(self.me, media=False)
        PostMedia.objects.bulk_create([PostMedia(
            post=post, media_type="image", file="posts/i.jpg", file_name="i.jpg", file_size_bytes=1, mime_type="image/jpeg",
        )])
        media = PostMedia.objects.get(post=post)
        self.assertEqual(self.post_retry(media).status_code, 400)

    def test_login_is_required(self):
        _, media = self.video()
        self.client.force_authenticate(None)
        self.assertIn(self.post_retry(media).status_code, (401, 403))

    @override_settings(VIDEO_PROCESSING_MANUAL_RETRY_LIMIT=2)
    def test_manual_retry_limit_gives_429(self):
        _, media = self.video(pipeline={"manual_retries": 2})
        with mock.patch.object(tasks.process_video_for_streaming, "delay") as delay:
            r = self.post_retry(media)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.data["processing_status"], "failed")
        delay.assert_not_called()
        self.assertEqual(self.status_of(media), "failed")

    @override_settings(VIDEO_PROCESSING_MANUAL_RETRY_LIMIT=2)
    def test_last_allowed_manual_retry_still_works(self):
        _, media = self.video(pipeline={"manual_retries": 1})
        with mock.patch.object(tasks.process_video_for_streaming, "delay"):
            r = self.post_retry(media)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.pipe_of(media)["manual_retries"], 2)

    @override_settings(VIDEO_TRANSCODE_ENABLED=False)
    def test_processing_switched_off_gives_503(self):
        _, media = self.video()
        with mock.patch.object(tasks.process_video_for_streaming, "delay") as delay:
            r = self.post_retry(media)
        self.assertEqual(r.status_code, 503)
        delay.assert_not_called()
        self.assertEqual(self.status_of(media), "failed")

    def test_queue_down_gives_503_and_row_goes_back_to_failed(self):
        _, media = self.video()
        with mock.patch.object(tasks.process_video_for_streaming, "delay", side_effect=RuntimeError("broker down")):
            r = self.post_retry(media)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.data["processing_status"], "failed")
        self.assertEqual(self.status_of(media), "failed")


# ----------------------------------------------------------------------------- R-B6 stuck / failed sweeper
@override_settings(VIDEO_TRANSCODE_ENABLED=True, VIDEO_PROCESSING_MAX_ATTEMPTS=3,
                   VIDEO_PROCESSING_RETRY_BACKOFF_MINUTES=10, VIDEO_PROCESSING_STUCK_MINUTES=120)
class StuckSweeperTests(_MediaBase):
    def sweep(self, **kw):
        with mock.patch.object(tasks.process_video_for_streaming, "delay") as delay:
            out = tasks.retry_failed_video_processing(**kw)
        return out, delay

    @staticmethod
    def ago(minutes):
        return (timezone.now() - datetime.timedelta(minutes=minutes)).isoformat()

    # --- failed rows
    def test_failed_with_attempts_left_is_requeued(self):
        _, media = self.video(pipeline={"attempts": 1})
        out, delay = self.sweep()
        self.assertEqual(out, {"retried": 1, "stuck": 0, "exhausted": 0})
        delay.assert_called_once_with(str(media.id))
        self.assertEqual(self.status_of(media), "pending")

    def test_failed_without_any_pipeline_info_is_requeued(self):
        _, media = self.video(pipeline=None)
        out, delay = self.sweep()
        self.assertEqual(out["retried"], 1)
        delay.assert_called_once_with(str(media.id))

    def test_exhausted_rows_are_counted_and_left_alone(self):
        _, media = self.video(pipeline={"attempts": 3})
        out, delay = self.sweep()
        self.assertEqual(out, {"retried": 0, "stuck": 0, "exhausted": 1})
        delay.assert_not_called()
        self.assertEqual(self.status_of(media), "failed")

    def test_backoff_grows_with_the_attempt_number(self):
        _, one = self.video(pipeline={"attempts": 1, "failed_at": self.ago(5)})    # needs 10 min -> too early
        _, two = self.video(pipeline={"attempts": 2, "failed_at": self.ago(15)})   # needs 20 min -> too early
        out, delay = self.sweep()
        self.assertEqual(out["retried"], 0)
        delay.assert_not_called()
        PostMedia.objects.filter(pk=one.pk).update(metadata={"pipeline": {"attempts": 1, "failed_at": self.ago(11)}})
        PostMedia.objects.filter(pk=two.pk).update(metadata={"pipeline": {"attempts": 2, "failed_at": self.ago(21)}})
        out, delay = self.sweep()
        self.assertEqual(out["retried"], 2)
        self.assertEqual(delay.call_count, 2)

    def test_garbage_failed_at_does_not_block_the_retry(self):
        _, media = self.video(pipeline={"attempts": 1, "failed_at": "not a date"})
        out, _ = self.sweep()
        self.assertEqual(out["retried"], 1)

    def test_batch_size_limits_the_retries(self):
        medias = [self.video()[1] for _ in range(3)]
        out, delay = self.sweep(batch_size=2)
        self.assertEqual(out["retried"], 2)
        self.assertEqual(delay.call_count, 2)
        self.assertEqual(sum(1 for m in medias if self.status_of(m) == "failed"), 1)

    def test_only_videos_are_swept(self):
        post = self.reel(self.me, media=False)
        PostMedia.objects.bulk_create([PostMedia(
            post=post, media_type="image", file="posts/i.jpg", file_name="i.jpg", file_size_bytes=1,
            mime_type="image/jpeg", processing_status=PostMedia.PROCESSING_FAILED,
        )])
        out, delay = self.sweep()
        self.assertEqual(out, {"retried": 0, "stuck": 0, "exhausted": 0})
        delay.assert_not_called()

    # --- regression: a failed OLD upload must be queued once, not again as "stuck"
    def test_old_failed_upload_is_queued_exactly_once(self):
        _, media = self.video(pipeline={"attempts": 1}, age_minutes=5 * 60)
        out, delay = self.sweep()
        self.assertEqual(out, {"retried": 1, "stuck": 0, "exhausted": 0})
        self.assertEqual(delay.call_count, 1)
        self.assertEqual(self.status_of(media), "pending")

    # --- stuck rows (worker died)
    def test_old_pending_is_requeued_as_stuck(self):
        _, media = self.video(status="pending", age_minutes=5 * 60)
        out, delay = self.sweep()
        self.assertEqual(out, {"retried": 0, "stuck": 1, "exhausted": 0})
        delay.assert_called_once_with(str(media.id))
        self.assertEqual(self.status_of(media), "pending")

    def test_old_processing_is_requeued_as_stuck(self):
        _, media = self.video(status="processing", age_minutes=5 * 60)
        out, delay = self.sweep()
        self.assertEqual(out["stuck"], 1)
        self.assertEqual(self.status_of(media), "pending")

    def test_a_worker_that_really_started_recently_is_left_alone(self):
        _, media = self.video(status="processing", age_minutes=5 * 60, pipeline={"started_at": self.ago(10)})
        out, delay = self.sweep()
        self.assertEqual(out["stuck"], 0)
        delay.assert_not_called()
        self.assertEqual(self.status_of(media), "processing")

    def test_a_started_at_that_is_also_old_does_not_protect_the_row(self):
        _, media = self.video(status="processing", age_minutes=5 * 60, pipeline={"started_at": self.ago(4 * 60)})
        out, _ = self.sweep()
        self.assertEqual(out["stuck"], 1)

    def test_recent_pending_is_not_stuck(self):
        _, media = self.video(status="pending", age_minutes=5)
        out, delay = self.sweep()
        self.assertEqual(out, {"retried": 0, "stuck": 0, "exhausted": 0})
        delay.assert_not_called()

    def test_ready_rows_are_never_touched(self):
        _, media = self.video(status="ready", age_minutes=5 * 60)
        out, delay = self.sweep()
        self.assertEqual(out, {"retried": 0, "stuck": 0, "exhausted": 0})
        delay.assert_not_called()

    @override_settings(VIDEO_PROCESSING_STUCK_MINUTES=5)
    def test_stuck_window_has_a_10_minute_floor(self):
        _, young = self.video(status="pending", age_minutes=8)
        _, old = self.video(status="pending", age_minutes=11)
        out, delay = self.sweep()
        self.assertEqual(out["stuck"], 1)
        delay.assert_called_once_with(str(old.id))

    # --- switches / failures
    @override_settings(VIDEO_TRANSCODE_ENABLED=False)
    def test_nothing_happens_when_processing_is_switched_off(self):
        _, media = self.video()
        self.video(status="pending", age_minutes=5 * 60)
        out, delay = self.sweep()
        self.assertEqual(out, {"retried": 0, "stuck": 0, "exhausted": 0})
        delay.assert_not_called()
        self.assertEqual(self.status_of(media), "failed")

    def test_queue_down_keeps_failed_rows_failed(self):
        _, media = self.video()
        _, waiting = self.video(status="pending", age_minutes=5 * 60)
        with mock.patch.object(tasks.process_video_for_streaming, "delay", side_effect=RuntimeError("broker down")):
            out = tasks.retry_failed_video_processing()
        self.assertEqual(out, {"retried": 0, "stuck": 0, "exhausted": 0})
        self.assertEqual(self.status_of(media), "failed")
        self.assertEqual(self.status_of(waiting), "failed")  # `_requeue` marks it failed so the next beat tries again


# ----------------------------------------------------------------------------- R-B6 failure bookkeeping + notify
@override_settings(VIDEO_PROCESSING_MAX_ATTEMPTS=3)
class MarkFailedTests(_MediaBase):
    def test_first_failure_counts_and_does_not_notify(self):
        _, media = self.video(status="processing")
        with mock.patch.object(tasks, "notify_video_processing_failed") as notify:
            attempts = tasks._mark_processing_failed(media.id, "ffmpeg exploded")
        self.assertEqual(attempts, 1)
        self.assertEqual(self.status_of(media), "failed")
        pipe = self.pipe_of(media)
        self.assertEqual((pipe["attempts"], pipe["last_error"]), (1, "ffmpeg exploded"))
        self.assertTrue(pipe["failed_at"])
        notify.assert_not_called()

    def test_error_text_is_cut_to_200_characters(self):
        _, media = self.video(status="processing")
        with mock.patch.object(tasks, "notify_video_processing_failed"):
            tasks._mark_processing_failed(media.id, "x" * 500)
        self.assertEqual(len(self.pipe_of(media)["last_error"]), 200)

    def test_final_failure_notifies_once_and_remembers_it(self):
        _, media = self.video(status="processing", pipeline={"attempts": 2})
        with mock.patch.object(tasks, "notify_video_processing_failed", return_value=True) as notify:
            attempts = tasks._mark_processing_failed(media.id, "bad codec")
            again = tasks._mark_processing_failed(media.id, "bad codec")  # a later final failure must not notify twice
        self.assertEqual(attempts, 3)
        self.assertEqual(again, 4)
        notify.assert_called_once_with(media.id)
        self.assertTrue(self.pipe_of(media)["notified"])

    def test_failed_notification_is_retried_on_the_next_final_failure(self):
        _, media = self.video(status="processing", pipeline={"attempts": 2})
        with mock.patch.object(tasks, "notify_video_processing_failed", return_value=False) as notify:
            tasks._mark_processing_failed(media.id, "bad codec")
            self.assertFalse(self.pipe_of(media).get("notified"))
            tasks._mark_processing_failed(media.id, "bad codec")
        self.assertEqual(notify.call_count, 2)

    def test_manual_retry_clears_notified_so_a_new_final_failure_notifies_again(self):
        _, media = self.video(status="processing", pipeline={"attempts": 2, "notified": True})
        with mock.patch.object(tasks, "notify_video_processing_failed", return_value=True) as notify:
            tasks._mark_processing_failed(media.id, "x")
            notify.assert_not_called()  # still marked as notified
            tasks._pipeline_meta_update(media.id, attempts=2, notified=False)  # what the retry endpoint does
            tasks._mark_processing_failed(media.id, "x")
            notify.assert_called_once_with(media.id)


class NotifyFailedTests(_MediaBase):
    def run_notify(self, media_id, **create_kw):
        with mock.patch("core.services.create_notification", **create_kw) as create, \
                mock.patch("post.services._push_row") as push:
            result = tasks.notify_video_processing_failed(media_id)
        return result, create, push

    def test_owner_gets_one_generic_row_and_a_push(self):
        post, media = self.video()
        result, create, push = self.run_notify(media.id)
        self.assertTrue(result)
        create.assert_called_once()
        args, kwargs = create.call_args
        self.assertEqual(args[0], post.user)
        from core.models import Notification

        self.assertEqual(args[1], Notification.NotifType.GENERIC)
        self.assertEqual(kwargs["data"], {
            "kind": "video_processing_failed", "post_id": str(post.id), "media_id": str(media.id), "action": "retry_processing",
        })
        push.assert_called_once_with(create.return_value)

    def test_deleted_post_gets_no_notification(self):
        post, media = self.video()
        Post.objects.filter(pk=post.pk).update(is_deleted=True)
        result, create, push = self.run_notify(media.id)
        self.assertFalse(result)
        create.assert_not_called()
        push.assert_not_called()

    def test_unknown_media_gets_no_notification(self):
        result, create, _ = self.run_notify(uuid.uuid4())
        self.assertFalse(result)
        create.assert_not_called()

    def test_a_crash_in_the_notification_code_never_raises(self):
        _, media = self.video()
        result, _, _ = self.run_notify(media.id, side_effect=RuntimeError("db down"))
        self.assertFalse(result)

    def test_no_row_written_means_false(self):
        _, media = self.video()
        result, _, _ = self.run_notify(media.id, return_value=None)
        self.assertFalse(result)


# ----------------------------------------------------------------------------- R-B4 feed card chip
class _FakePost:
    def __init__(self, metadata, post_type="video"):
        self.metadata = metadata
        self.post_type = post_type


class FeedStudyChipTests(SimpleTestCase):
    def test_none_without_any_study_info(self):
        for meta in ({}, None, {"reel": "x"}, {"reel": {"v": 1, "clips": 1, "duration": 5}}):
            self.assertIsNone(feed_study_chip(_FakePost(meta)), repr(meta))

    def test_series_and_chapter_count_only(self):
        reel = {"v": 1, "series": {"title": "Algebra", "part": 2, "total": 5},
                "chapters": [{"t": 0, "title": "A"}, {"t": 10, "title": "B"}], "subject": "maths", "exams": ["jee"],
                "captions": {"cues": []}, "music": {"id": "1"}}
        self.assertEqual(feed_study_chip(_FakePost({"reel": reel})),
                         {"series": {"title": "Algebra", "part": 2, "total": 5}, "chapters": 2})

    def test_unknown_total_is_zero(self):
        out = feed_study_chip(_FakePost({"reel": {"series": {"title": "Algebra", "part": 1, "total": 0}}}))
        self.assertEqual(out, {"series": {"title": "Algebra", "part": 1, "total": 0}})
        out = feed_study_chip(_FakePost({"reel": {"series": {"title": "Algebra", "part": 1}}}))
        self.assertEqual(out["series"]["total"], 0)

    def test_chapters_only_and_series_only(self):
        self.assertEqual(feed_study_chip(_FakePost({"reel": {"chapters": [{"t": 0, "title": "A"}]}})), {"chapters": 1})
        self.assertEqual(feed_study_chip(_FakePost({"reel": {"series": {"title": "A", "part": 3, "total": 3}}})),
                         {"series": {"title": "A", "part": 3, "total": 3}})

    def test_subject_notes_and_test_alone_do_not_make_a_chip(self):
        reel = {"subject": "maths", "exams": ["jee"], "notes": {"post_id": "x", "title": "N"}, "test": {"series_id": "y", "title": "T"}}
        self.assertIsNone(feed_study_chip(_FakePost({"reel": reel})))

    def test_series_without_a_title_is_ignored(self):
        self.assertIsNone(feed_study_chip(_FakePost({"reel": {"series": {"part": 1, "total": 3}}})))


class FeedStudyChipSerializerTests(SimpleTestCase):
    """PostListSerializer.get_reel_study: video posts only, never the whole metadata."""

    def get(self, post):
        return PostListSerializer().get_reel_study(post)

    def test_video_post_gets_the_chip(self):
        post = _FakePost({"reel": {"series": {"title": "A", "part": 1, "total": 3}}})
        self.assertEqual(self.get(post), {"series": {"title": "A", "part": 1, "total": 3}})

    def test_other_post_types_get_none(self):
        meta = {"reel": {"series": {"title": "A", "part": 1, "total": 3}}}
        for kind in ("text", "image", "doubt", "repost"):
            self.assertIsNone(self.get(_FakePost(meta, post_type=kind)), kind)

    def test_metadata_itself_is_not_in_the_list_fields(self):
        self.assertIn("reel_study", PostListSerializer.Meta.fields)
        self.assertNotIn("metadata", PostListSerializer.Meta.fields)
