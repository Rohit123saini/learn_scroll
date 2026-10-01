# post/tests_watch_time.py
# Feed ranking - PART 1: watch-time. `Post.video_watch_count` / `video_avg_watch_seconds`
# (kept by the PostView signal) + feed_mix.video_watch_boost() in the video term of every score.
#
#   PureFormulaTests   confidence + points (no DB)
#   SignalTests        PostView save -> rate / count / avg seconds on the Post
#   SqlMirrorTests     the SQL expression == the pure formula (they must never drift)
#   RankingTests       feed_mix.build_pool_ids: evidence + watch time change the order
#   ExploreTests       same boost through GET /post/explore/
#   BackfillTests      migration 0005 backfill + recompute_video_watch_stats command
from datetime import timedelta
from importlib import import_module

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from user_profile.models import Follow

from . import feed_mix
from .models import Post, PostView
from .views import _video_and_velocity_boost

User = get_user_model()
CFG = feed_mix.DEFAULT_WATCH_TIME


def make_post(user, content, **kw):
    kw.setdefault("visibility", "public")
    kw.setdefault("moderation_status", "approved")
    kw.setdefault("post_type", "video")
    kw.setdefault("category", "tech")
    return Post.objects.create(user=user, content=content, **kw)


def watch(post, viewer, seconds, duration):
    return PostView.objects.create(post=post, user=viewer, watch_seconds=seconds,
                                   video_duration_seconds=duration, is_counted=False)


class Viewers:
    """A pool of throw-away viewer accounts (PostView is unique per (post, user))."""
    def make_viewers(self, n):
        return [User.objects.create_user(username=f"viewer{i}", password="x") for i in range(n)]

    def watch_many(self, post, viewers, seconds, duration):
        for v in viewers:
            watch(post, v, seconds, duration)


class PureFormulaTests(SimpleTestCase):
    def test_confidence(self):
        self.assertEqual(feed_mix.watch_confidence(0, 5), 0.0)
        self.assertAlmostEqual(feed_mix.watch_confidence(1, 5), 1 / 6)
        self.assertAlmostEqual(feed_mix.watch_confidence(5, 5), 0.5)
        self.assertAlmostEqual(feed_mix.watch_confidence(45, 5), 0.9)
        self.assertEqual(feed_mix.watch_confidence(3, 0), 1.0)  # k <= 0 -> no damping
        self.assertEqual(feed_mix.watch_confidence(None, 5), 0.0)

    def test_one_perfect_viewer_is_worth_far_less_than_the_old_flat_30(self):
        one = feed_mix.watch_boost_points(1.0, 1, 30, CFG)  # (30 + 30/60*10) * 1/6
        self.assertAlmostEqual(one, 35 / 6)
        self.assertLess(one, 30)

    def test_many_viewers_approach_the_cap(self):
        big = feed_mix.watch_boost_points(1.0, 10_000, 600, CFG)
        self.assertAlmostEqual(big, 40.0, delta=0.05)  # 30 completion + 10 watch time

    def test_absolute_watch_time_separates_equal_completion(self):
        loop = feed_mix.watch_boost_points(1.0, 50, 5, CFG)      # finished a 5 s loop
        lesson = feed_mix.watch_boost_points(1.0, 50, 300, CFG)  # finished a 5 min lesson
        self.assertGreater(lesson, loop)

    def test_seconds_part_is_capped(self):
        self.assertEqual(feed_mix.watch_boost_points(0.0, 10, 60, CFG), feed_mix.watch_boost_points(0.0, 10, 6000, CFG))

    def test_never_negative_and_zero_without_evidence(self):
        self.assertEqual(feed_mix.watch_boost_points(0.8, 0, 40, CFG), 0.0)
        self.assertEqual(feed_mix.watch_boost_points(0.0, 10, 0, CFG), 0.0)
        self.assertGreaterEqual(feed_mix.watch_boost_points(-3, 5, -9, CFG), 0.0)
        self.assertLessEqual(feed_mix.watch_boost_points(9, 10**6, 10**6, CFG), 40.0 + 1e-9)  # rate clamped to 1

    def test_disabled_is_the_legacy_formula(self):
        legacy = dict(CFG, enabled=False)
        self.assertAlmostEqual(feed_mix.watch_boost_points(0.5, 1, 999, legacy), 15.0)
        self.assertAlmostEqual(feed_mix.watch_boost_points(0.5, 0, 0, legacy), 15.0)


class SignalTests(TestCase, Viewers):
    def setUp(self):
        self.author = User.objects.create_user(username="author", password="x")
        self.post = make_post(self.author, "v")

    def fresh(self):
        self.post.refresh_from_db()
        return self.post

    def test_stats_follow_the_views(self):
        a, b = self.make_viewers(2)
        watch(self.post, a, 30, 60)
        p = self.fresh()
        self.assertEqual((p.video_watch_count, p.video_avg_watch_seconds, p.video_completion_rate), (1, 30.0, 0.5))
        watch(self.post, b, 60, 60)
        p = self.fresh()
        self.assertEqual(p.video_watch_count, 2)
        self.assertAlmostEqual(p.video_avg_watch_seconds, 45.0)
        self.assertAlmostEqual(p.video_completion_rate, 0.75)

    def test_a_furthest_point_update_changes_the_average(self):
        (a,) = self.make_viewers(1)
        pv = watch(self.post, a, 10, 100)
        pv.watch_seconds = 50
        pv.save()
        p = self.fresh()
        self.assertEqual((p.video_watch_count, p.video_avg_watch_seconds), (1, 50.0))

    def test_watch_seconds_are_clamped_to_the_duration(self):
        (a,) = self.make_viewers(1)
        watch(self.post, a, 500, 60)  # bogus client value
        p = self.fresh()
        self.assertEqual((p.video_avg_watch_seconds, p.video_completion_rate), (60.0, 1.0))

    def test_plain_views_and_non_video_posts_do_not_count(self):
        a, b = self.make_viewers(2)
        PostView.objects.create(post=self.post, user=a)  # opened, no progress data
        text = make_post(self.author, "t", post_type="text")
        watch(text, b, 10, 20)
        self.assertEqual(self.fresh().video_watch_count, 0)
        text.refresh_from_db()
        self.assertEqual((text.video_watch_count, text.video_avg_watch_seconds), (0, 0.0))

    def test_progress_endpoint_feeds_the_stats(self):
        from rest_framework.test import APIClient
        from .models import PostMedia
        # bulk_create: skips post_save, so no thumbnail task / ffmpeg is triggered in the test.
        PostMedia.objects.bulk_create([PostMedia(
            post=self.post, media_type="video", duration_seconds=100, file="post_media/x.mp4",
            file_name="x.mp4", file_size_bytes=1, mime_type="video/mp4",
        )])
        (a,) = self.make_viewers(1)
        c = APIClient()
        c.force_authenticate(a)
        r = c.post(reverse("post-video-progress", kwargs={"post_id": self.post.id}), {"watched_seconds": 40}, format="json")
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        p = self.fresh()
        self.assertEqual((p.video_watch_count, p.video_avg_watch_seconds), (1, 40.0))


class SqlMirrorTests(TestCase, Viewers):
    """The SQL expression must equal the pure formula for every kind of post."""

    def boost_of(self, post):
        return Post.objects.annotate(b=feed_mix.video_watch_boost()).get(pk=post.pk).b

    def check(self, post):
        post.refresh_from_db()
        want = feed_mix.watch_boost_points(post.video_completion_rate, post.video_watch_count,
                                           post.video_avg_watch_seconds, feed_mix.get_watch_time_config())
        self.assertAlmostEqual(self.boost_of(post), want, places=6)
        return want

    def test_matches_for_various_videos(self):
        author = User.objects.create_user(username="author", password="x")
        viewers = self.make_viewers(12)
        one = make_post(author, "one")
        watch(one, viewers[0], 30, 30)
        many = make_post(author, "many")
        for i, v in enumerate(viewers):
            watch(many, v, 10 + i * 4, 60)
        short = make_post(author, "short")
        self.watch_many(short, viewers[:6], 5, 5)
        unseen = make_post(author, "unseen")
        text = make_post(author, "text", post_type="text")
        vals = [self.check(p) for p in (one, many, short, unseen, text)]
        self.assertEqual(vals[3:], [0.0, 0.0])
        self.assertTrue(all(v > 0 for v in vals[:3]))

    def test_text_post_is_zero_even_with_stale_watch_columns(self):
        author = User.objects.create_user(username="author", password="x")
        text = make_post(author, "t", post_type="text", video_completion_rate=1.0, video_watch_count=99,
                         video_avg_watch_seconds=99.0)
        self.assertEqual(self.boost_of(text), 0.0)

    @override_settings(FEED_WATCH_TIME={"enabled": False})
    def test_matches_in_legacy_mode(self):
        author = User.objects.create_user(username="author", password="x")
        (v,) = self.make_viewers(1)
        p = make_post(author, "p")
        watch(p, v, 15, 30)
        self.assertAlmostEqual(self.check(p), 15.0)

    @override_settings(FEED_WATCH_TIME={"confidence_k": 0, "watch_seconds_cap": 0})
    def test_matches_with_degenerate_settings(self):
        author = User.objects.create_user(username="author", password="x")
        (v,) = self.make_viewers(1)
        p = make_post(author, "p")
        watch(p, v, 15, 30)
        self.assertAlmostEqual(self.check(p), 15.0)  # no damping, no seconds term

    @override_settings(FEED_WATCH_TIME={"completion_points": 10.0, "watch_seconds_points": 0.0, "confidence_k": 1.0})
    def test_matches_with_custom_settings(self):
        author = User.objects.create_user(username="author", password="x")
        viewers = self.make_viewers(3)
        p = make_post(author, "p")
        self.watch_many(p, viewers, 30, 60)
        self.assertAlmostEqual(self.check(p), 0.75 * 0.5 * 10.0)


class RankingTests(TestCase, Viewers):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.x = User.objects.create_user(username="xavier", password="x")
        self.y = User.objects.create_user(username="yara", password="x")
        self.viewers = self.make_viewers(10)

    def pools(self):
        base = Post.objects.filter(is_deleted=False).exclude(user=self.me)
        return feed_mix.build_pool_ids(self.me, base, set(), _video_and_velocity_boost)

    def order(self, source="trending"):
        ids = self.pools()[source]
        by_id = dict(Post.objects.filter(id__in=ids).values_list("id", "content"))
        return [by_id[i] for i in ids]

    def two_videos(self):
        lucky = make_post(self.x, "lucky")   # ONE viewer, finished it
        proven = make_post(self.y, "proven")  # ten viewers, half of a minute each
        watch(lucky, self.viewers[0], 60, 60)
        self.watch_many(proven, self.viewers, 30, 60)
        return lucky, proven

    def test_evidence_beats_a_single_perfect_viewer(self):
        self.two_videos()
        # old formula: lucky 30 > proven 15.  now: lucky 5.8 < proven 13.3
        self.assertEqual(self.order(), ["proven", "lucky"])

    @override_settings(FEED_WATCH_TIME={"enabled": False})
    def test_legacy_switch_restores_the_old_order(self):
        self.two_videos()
        self.assertEqual(self.order(), ["lucky", "proven"])

    def test_absolute_watch_time_breaks_the_tie_between_equal_completion(self):
        loop = make_post(self.x, "loop")
        lesson = make_post(self.y, "lesson")
        self.watch_many(loop, self.viewers, 5, 5)       # 100% of 5 s
        self.watch_many(lesson, self.viewers, 300, 300)  # 100% of 5 min
        self.assertEqual(self.order(), ["lesson", "loop"])

    def test_applies_to_recommended_and_following_too(self):
        lucky, proven = self.two_videos()
        Post.objects.filter(pk__in=[lucky.pk, proven.pk]).update(created_at=timezone.now() - timedelta(days=10))
        self.assertEqual(self.pools()["trending"], [])  # outside the 7-day trending window
        self.assertEqual(self.order("recommended"), ["proven", "lucky"])
        Follow.objects.create(follower=self.me, following=self.x, status=Follow.Status.ACCEPTED)
        Follow.objects.create(follower=self.me, following=self.y, status=Follow.Status.ACCEPTED)
        base = Post.objects.filter(is_deleted=False).exclude(user=self.me)
        pools = feed_mix.build_pool_ids(self.me, base, {self.x.id, self.y.id}, _video_and_velocity_boost)
        by_id = dict(Post.objects.filter(id__in=pools["following"]).values_list("id", "content"))
        self.assertEqual([by_id[i] for i in pools["following"]], ["proven", "lucky"])

    def test_never_removes_or_demotes_below_a_plain_post(self):
        plain = make_post(self.x, "plain", post_type="text")
        bad = make_post(self.y, "bad-video")
        self.watch_many(bad, self.viewers, 0, 60)  # everybody scrolled away at once
        pools = self.pools()["trending"]
        self.assertEqual(len(pools), 2)
        self.assertEqual(self.order(), ["bad-video", "plain"])  # boost 0 == plain, newer first: not pushed below it
        self.assertIsNotNone(plain)

    def test_unwatched_videos_and_text_posts_are_unaffected(self):
        make_post(self.x, "a")
        make_post(self.y, "b", post_type="text")
        self.assertEqual(self.order(), ["b", "a"])  # pure recency, as before


class ExploreTests(APITestCase, Viewers):
    def test_explore_uses_the_same_boost(self):
        cache.clear()
        me = User.objects.create_user(username="me", password="x")
        x = User.objects.create_user(username="xavier", password="x")
        y = User.objects.create_user(username="yara", password="x")
        viewers = self.make_viewers(10)
        lucky = make_post(x, "lucky")
        proven = make_post(y, "proven")
        watch(lucky, viewers[0], 60, 60)
        self.watch_many(proven, viewers, 30, 60)
        self.client.force_authenticate(me)
        r = self.client.get(reverse("post-explore"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([p["content"] for p in r.data["results"]], ["proven", "lucky"])


class BackfillTests(TestCase, Viewers):
    def setUp(self):
        self.author = User.objects.create_user(username="author", password="x")
        self.post = make_post(self.author, "v")
        viewers = self.make_viewers(3)
        watch(self.post, viewers[0], 20, 40)
        watch(self.post, viewers[1], 40, 40)
        watch(self.post, viewers[2], 10, 40)
        self.empty = make_post(self.author, "empty", video_watch_count=7, video_avg_watch_seconds=9.0)
        Post.objects.filter(pk=self.post.pk).update(video_completion_rate=0.0, video_watch_count=0,
                                                    video_avg_watch_seconds=0.0)

    def test_migration_backfill(self):
        mod = import_module("post.migrations.0005_post_watch_time_stats")
        mod.backfill_watch_stats(django_apps, None)
        self.post.refresh_from_db()
        self.assertEqual(self.post.video_watch_count, 3)
        self.assertAlmostEqual(self.post.video_avg_watch_seconds, 70 / 3)
        self.assertAlmostEqual(self.post.video_completion_rate, (0.5 + 1.0 + 0.25) / 3)

    def test_command_recomputes_and_reset_zeroes_videos_without_data(self):
        call_command("recompute_video_watch_stats", verbosity=0)
        self.post.refresh_from_db()
        self.empty.refresh_from_db()
        self.assertEqual(self.post.video_watch_count, 3)
        self.assertEqual(self.empty.video_watch_count, 7)  # untouched without --reset
        call_command("recompute_video_watch_stats", "--reset", verbosity=0)
        self.empty.refresh_from_db()
        self.assertEqual((self.empty.video_watch_count, self.empty.video_avg_watch_seconds), (0, 0.0))
