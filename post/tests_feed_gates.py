# post/tests_feed_gates.py - T1 items 6-10 on top of Parts 3-5:
#   blocked never leaks (every source), A/B overrides of mix / diversity / author_cap / quality,
#   central config + explain, candidate cache <-> snapshot consistency, N+1 guard,
#   daily metrics task, benchmark command.
import io
import uuid
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from user_profile.models import BlockUser, Follow

from . import feed_cache, feed_config, feed_experiment, feed_mix
from .models import FeedFeedback, Post, PostEvent

User = get_user_model()
NO_EXPLORE = {"enabled": False}


def mk(user, content, age_minutes=0, **kw):
    kw.setdefault("category", "tech")
    p = Post.objects.create(user=user, content=content, post_type="text", visibility="public",
                            moderation_status="approved", **kw)
    Post.objects.filter(pk=p.pk).update(created_at=timezone.now() - timedelta(minutes=age_minutes))
    return p


def variant(**overrides):
    """FEED_EXPERIMENT with ONE variant that every user lands in."""
    return {"variants": [{"name": "only", "weight": 100, "overrides": overrides}]}


class _Base(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="g_me", password="x")
        self.friend = User.objects.create_user(username="g_friend", password="x")
        self.rival = User.objects.create_user(username="g_rival", password="x")
        self.other = User.objects.create_user(username="g_other", password="x")
        self.client.force_authenticate(self.me)
        self.url = reverse("home-feed")

    def _feed(self, **params):
        params.setdefault("page_size", 50)
        r = self.client.get(self.url, params)
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        return r

    def _authors(self, r):
        ids = [uuid.UUID(str(x["id"])) for x in r.data["results"]]
        by_id = dict(Post.objects.filter(id__in=ids).values_list("id", "user_id"))
        return [by_id[i] for i in ids]


# ---------------------------------------------------------------------------
# ITEM 6 - blocked / muted content never leaks, from ANY candidate source
@override_settings(FEED_EXPLORE=NO_EXPLORE)
class BlockedNeverLeaksTests(_Base):
    def setUp(self):
        super().setUp()
        Follow.objects.create(follower=self.me, following=self.friend, status=Follow.Status.ACCEPTED)
        for i in range(6):
            mk(self.friend, f"friend post number {i} about physics", i, likes_count=i)
            mk(self.rival, f"rival post number {i} about physics", i, likes_count=i + 50)  # trending + recommended
            mk(self.other, f"other post number {i} about physics", i, likes_count=i)

    def test_blocked_author_absent_from_following_trending_and_recommended(self):
        BlockUser.objects.create(blocker=self.me, blocked=self.friend)  # a FOLLOWED author, then blocked
        BlockUser.objects.create(blocker=self.me, blocked=self.rival)  # a trending stranger
        for params in ({}, {"source": "following"}, {"refresh": 1}):
            authors = set(self._authors(self._feed(**params)))
            self.assertNotIn(self.friend.id, authors, params)
            self.assertNotIn(self.rival.id, authors, params)

    def test_blocked_by_the_other_side_is_also_hidden(self):
        BlockUser.objects.create(blocker=self.rival, blocked=self.me)
        self.assertNotIn(self.rival.id, set(self._authors(self._feed())))

    def test_block_after_page_one_is_not_served_from_the_frozen_snapshot(self):
        first = self._feed(page_size=6)
        self.assertTrue(first.data["next"])
        with self.captureOnCommitCallbacks(execute=True):
            BlockUser.objects.create(blocker=self.me, blocked=self.rival)
        rest, r = [], first
        while r.data["next"]:
            r = self.client.get(r.data["next"])
            self.assertEqual(r.status_code, 200)
            rest += self._authors(r)
        self.assertNotIn(self.rival.id, rest)

    def test_block_after_a_cached_session_does_not_leak_from_cached_candidates(self):
        self._feed()  # fills the 30 s candidate cache
        with self.captureOnCommitCallbacks(execute=True):
            BlockUser.objects.create(blocker=self.me, blocked=self.rival)
        self.assertNotIn(self.rival.id, set(self._authors(self._feed())))

    def test_block_live_drops_both_users_cached_candidates(self):
        self._feed()
        rec = feed_mix.normalize_ratios(feed_mix.get_ratios())[feed_mix.SOURCE_RECOMMENDED]
        salt = feed_cache.salt_for(self.me)
        self.assertIsNotNone(feed_cache.get(self.me.pk, None, rec, salt))
        with self.captureOnCommitCallbacks(execute=True):
            BlockUser.objects.create(blocker=self.me, blocked=self.rival)
        self.assertIsNone(feed_cache.get(self.me.pk, None, rec, salt))

    def test_new_creator_blocked_is_not_explored(self):
        newbie = User.objects.create_user(username="g_newbie", password="x")
        post = mk(newbie, "brand new creator first post about maths")
        BlockUser.objects.create(blocker=self.me, blocked=newbie)
        with override_settings(FEED_EXPLORE={"enabled": True}):
            ids = [x["id"] for x in self._feed().data["results"]]
        self.assertNotIn(str(post.id), ids)

    def test_heavily_reported_post_is_out_of_every_source_even_following(self):
        bad = mk(self.friend, "followed author but reported again and again", reported_count=10)
        with override_settings(FEED_QUALITY={"enabled": True}):
            ids = [x["id"] for x in self._feed().data["results"]]
            ids_following = [x["id"] for x in self._feed(source="following").data["results"]]
        self.assertNotIn(str(bad.id), ids)
        self.assertNotIn(str(bad.id), ids_following)


# ---------------------------------------------------------------------------
# ITEM 7 - one config, A/B overrides for mix / diversity / author_cap / quality
class ConfigAndVariantTests(_Base):
    def test_effective_config_lists_every_section_and_the_bucket(self):
        cfg = feed_config.effective(self.me.pk)
        for key in ("experiment", "mix", "limits", "seen", "diversity", "author_cap", "quality",
                    "explore", "signals", "context", "cache"):
            self.assertIn(key, cfg)
        self.assertTrue(0 <= cfg["experiment"]["bucket"] < 100)
        self.assertAlmostEqual(sum(cfg["mix"].values()), 1.0, places=3)

    def test_variant_overrides_show_up_in_effective_config(self):
        with override_settings(FEED_EXPERIMENT=variant(
                mix={"following": 0.2, "recommended": 0.7, "trending": 0.1},
                diversity={"author_gap": 6}, author_cap={"discovery_soft_cap": 1}, quality={"min_text_chars": 40})):
            cfg = feed_config.effective(self.me.pk)
        self.assertEqual(cfg["experiment"]["variant"], "only")
        self.assertEqual(cfg["experiment"]["overridden_sections"], ["author_cap", "diversity", "mix", "quality"])
        self.assertAlmostEqual(cfg["mix"][feed_mix.SOURCE_RECOMMENDED], 0.7, places=2)
        self.assertEqual(cfg["diversity"]["author_gap"], 6)
        self.assertEqual(cfg["author_cap"]["discovery_soft_cap"], 1)
        self.assertEqual(cfg["quality"]["min_text_chars"], 40)

    @override_settings(FEED_EXPLORE=NO_EXPLORE)
    def test_mix_override_changes_the_ratios_the_feed_uses(self):
        for i in range(10):
            mk(self.other, f"ratio experiment post {i} about biology", i, likes_count=i)
        with override_settings(FEED_EXPERIMENT=variant(mix={"following": 0.1, "recommended": 0.8, "trending": 0.1})), \
                mock.patch.object(feed_mix, "allocate_next", wraps=feed_mix.allocate_next) as spy:
            self._feed(page_size=5)
        ratios = spy.call_args[0][3]  # allocate_next(offsets, page_size, sizes, ratios)
        self.assertAlmostEqual(ratios[feed_mix.SOURCE_RECOMMENDED], 0.8, places=2)

    def test_author_cap_override_changes_pool_order(self):
        a = [mk(self.other, f"author a post {i} about thermodynamics", i) for i in range(3)]
        b = mk(self.rival, "author b post about thermodynamics")
        pools = {"following": [], "recommended": [a[0].id, a[1].id, a[2].id, b.id], "trending": []}
        default = feed_mix.apply_author_caps(dict(pools))["recommended"]  # soft cap 2: a2 is demoted
        strict = feed_mix.apply_author_caps(dict(pools), {"discovery_soft_cap": 1})["recommended"]  # soft cap 1
        self.assertEqual(default, [a[0].id, a[1].id, b.id, a[2].id])
        self.assertEqual(strict, [a[0].id, b.id, a[1].id, a[2].id])

    def test_get_ratios_override_is_normalised(self):
        r = feed_mix.get_ratios({"following": 1, "recommended": 1, "trending": 2})
        self.assertAlmostEqual(r["trending"], 0.5, places=3)

    @override_settings(FEED_EXPLORE=NO_EXPLORE)
    def test_why_config_is_staff_only(self):
        p = mk(self.other, "why config post about organic chemistry", likes_count=3)
        r = self.client.get(reverse("post-why", args=[p.id]))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("config", r.data)
        User.objects.filter(pk=self.me.pk).update(is_staff=True)
        self.me.refresh_from_db()
        self.client.force_authenticate(self.me)
        r = self.client.get(reverse("post-why", args=[p.id]))
        self.assertIn("mix", r.data["config"])
        self.assertIn("bucket", r.data["experiment"])


# ---------------------------------------------------------------------------
# ITEM 8 - candidate cache consistent with the snapshot; N+1 guard; benchmark command
@override_settings(FEED_EXPLORE=NO_EXPLORE)
class PerformanceTests(_Base):
    def setUp(self):
        super().setUp()
        for i in range(8):
            mk(self.other, f"candidate cache post {i} about chemistry", i, likes_count=i)

    def test_cache_hit_and_miss_page_one_identical_and_cursor_still_works(self):
        a = self._feed(page_size=4)
        b = self._feed(page_size=4)  # served from cached candidates
        self.assertEqual([x["id"] for x in a.data["results"]], [x["id"] for x in b.data["results"]])
        self.assertEqual(a.data["count"], b.data["count"])
        nxt = self.client.get(b.data["next"])  # the hit owns a working snapshot
        self.assertEqual(nxt.status_code, 200)
        self.assertEqual(len(nxt.data["results"]), 4)

    def test_cursor_request_never_rebuilds_pools(self):
        first = self._feed(page_size=3)
        with mock.patch.object(feed_mix, "build_pool_ids", wraps=feed_mix.build_pool_ids) as spy:
            for _ in range(2):
                self.assertEqual(self.client.get(first.data["next"]).status_code, 200)
        self.assertEqual(spy.call_count, 0)

    def test_query_count_does_not_grow_with_page_size(self):
        for i in range(30):
            mk(self.other, f"n plus one guard post {i} about maths", i, likes_count=i)

        def queries(page_size):
            cache.clear()
            with CaptureQueriesContext(connection) as ctx:
                r = self._feed(page_size=page_size, refresh=1)
            self.assertEqual(len(r.data["results"]), page_size)
            return len(ctx)

        small, big = queries(5), queries(25)
        self.assertLessEqual(big - small, 4, f"N+1? 5 posts={small} queries, 25 posts={big} queries")

    def test_benchmark_command_runs_and_reports_percentiles(self):
        out = io.StringIO()
        call_command("feed_benchmark", username=self.me.username, runs=3, page_size=5,
                     target_ms=60000.0, stdout=out)
        text = out.getvalue()
        self.assertIn("p95=", text)
        self.assertIn("PASS", text)


# ---------------------------------------------------------------------------
# ITEM 9 - nightly summary task
class DailyMetricsTaskTests(_Base):
    def test_task_returns_and_caches_the_days_summary(self):
        from . import tasks

        p = mk(self.other, "metrics post about physics today")
        PostEvent.objects.create(user=self.me, post=p, event_type="impression", surface="feed")
        PostEvent.objects.create(user=self.me, post=p, event_type="dwell", surface="feed", dwell_ms=4000)
        today = timezone.now().date().isoformat()
        FeedFeedback.objects.create(user=self.me, kind="category", key="tech")  # "show fewer like this"
        summary = tasks.feed_daily_metrics(today)
        self.assertEqual(summary["impressions"], 1)
        self.assertEqual(summary["show_fewer"], 1)
        self.assertEqual(summary["avg_dwell_ms"], 4000)
        self.assertEqual(summary["date"], today)
        self.assertEqual(cache.get(f"feed:metrics:{today}")["impressions"], 1)

    def test_beat_entry_is_registered(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["post-feed-daily-metrics"]
        self.assertEqual(entry["task"], "post.tasks.feed_daily_metrics")
