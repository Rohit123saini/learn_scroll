# post/tests_feed_resilience.py
"""
H10 - backend feed resilience.

  * ranking / build_pool_ids error or DB timeout  -> 200 with the fallback feed (following by recency + recent
    public), `X-Feed-Degraded: 1`, never a 500; even a failing fallback gives an empty 200, counted as an error
  * Redis (snapshot / candidate cache / throttle / counters) down -> page 1 AND page 2 still work (the miss path)
    and the outage is counted as an alert event (`snapshot_store_down`)
  * single-flight lock (cache-stampede)
  * explicit `feed_home` throttle scope on HomeFeedView (429 after the limit)
  * SLO numbers (post/feed_ops.py): availability, p95, breaches, alert
  * retention (post/feed_retention.py): PostEvent, seen PostView rows, PostView PII
  * GET /post/feed/ops/ (staff / scrape token) + Prometheus text
"""
import datetime
from contextlib import contextmanager
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework.throttling import SimpleRateThrottle

from user_profile.models import BlockUser, Follow

from . import feed_mix, feed_ops, feed_resilience, feed_retention, feed_snapshot
from .models import Post, PostEvent, PostView

User = get_user_model()


class _BrokenCache:
    """Every call fails like a dead Redis connection."""

    def __getattr__(self, name):
        def boom(*args, **kwargs):
            raise ConnectionError("redis is down")

        return boom


@contextmanager
def redis_down():
    broken = _BrokenCache()
    with mock.patch.object(feed_snapshot, "cache", broken), \
            mock.patch("post.feed_cache.cache", broken), \
            mock.patch.object(feed_resilience, "cache", broken), \
            mock.patch.object(feed_ops, "cache", broken), \
            mock.patch.object(SimpleRateThrottle, "cache", broken):
        yield


def _reset_ops():
    feed_ops._local.clear()
    cache.clear()


def _mk_post(user, content, minutes_ago=0, **kw):
    kw.setdefault("category", "tech")
    p = Post.objects.create(user=user, content=content, post_type="text", visibility="public",
                            moderation_status="approved", **kw)
    Post.objects.filter(pk=p.pk).update(created_at=timezone.now() - datetime.timedelta(days=10, minutes=minutes_ago))
    return p


# ---------------------------------------------------------------- fallback + redis down
class HomeFeedFallbackTests(APITestCase):
    def setUp(self):
        _reset_ops()
        self.me = User.objects.create_user(username="me", password="x")
        self.friend = User.objects.create_user(username="friend", password="x")
        self.stranger = User.objects.create_user(username="stranger", password="x")
        self.blocked = User.objects.create_user(username="blocked", password="x")
        Follow.objects.create(follower=self.me, following=self.friend, status=Follow.Status.ACCEPTED)
        BlockUser.objects.create(blocker=self.me, blocked=self.blocked)
        # friend's posts: i=0 is the NEWEST
        self.friend_posts = [_mk_post(self.friend, f"f{i}", minutes_ago=i) for i in range(12)]
        self.public_posts = [_mk_post(self.stranger, f"s{i}", minutes_ago=i) for i in range(6)]
        self.blocked_post = _mk_post(self.blocked, "nope")
        self.client.force_authenticate(self.me)
        self.url = reverse("home-feed")

    def _ids(self, r):
        return [x["id"] for x in r.data["results"]]

    def _walk(self, first):
        ids, r, n = self._ids(first), first, 0
        while r.data["next"]:
            n += 1
            self.assertLess(n, 20)
            r = self.client.get(r.data["next"])
            self.assertEqual(r.status_code, 200)
            ids += self._ids(r)
        return ids

    # ---- ranking blows up ----
    def test_ranking_error_serves_fallback_not_500(self):
        with mock.patch.object(feed_mix, "build_pool_ids", side_effect=RuntimeError("ranking exploded")):
            r = self.client.get(self.url, {"page_size": 5})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r[feed_resilience.DEGRADED_HEADER], "1")
        self.assertEqual(len(r.data["results"]), 5)
        self.assertEqual(set(r.data.keys()), {"count", "next", "previous", "results"})

    def test_fallback_is_following_by_recency_plus_recent_public(self):
        with mock.patch.object(feed_mix, "build_pool_ids", side_effect=RuntimeError("boom")):
            first = self.client.get(self.url, {"page_size": 6})
            ids = self._walk(first)
        friend_ids = [str(p.id) for p in self.friend_posts]
        public_ids = {str(p.id) for p in self.public_posts}
        self.assertEqual(len(ids), len(set(ids)), "no duplicates across fallback pages")
        self.assertNotIn(str(self.blocked_post.id), ids, "blocked author never appears, even in the fallback")
        self.assertTrue(public_ids <= set(ids), "recent public posts are part of the fallback")
        # the page-level diversity re-rank may shuffle INSIDE a page, but which followed posts land on page 1 is
        # decided by recency: they are exactly the newest k
        first_friend = [i for i in self._ids(first) if i in set(friend_ids)]
        self.assertEqual(set(first_friend), set(friend_ids[:len(first_friend)]), "following part is newest-first")
        self.assertTrue(set(friend_ids) <= set(ids))

    def test_fallback_pages_are_frozen_in_a_snapshot(self):
        with mock.patch.object(feed_mix, "build_pool_ids", side_effect=RuntimeError("boom")):
            first = self.client.get(self.url, {"page_size": 4})
        # ranking is healthy again on page 2: the cursor still continues the SAME (fallback) list
        second = self.client.get(first.data["next"])
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second[feed_resilience.DEGRADED_HEADER], "1")
        self.assertFalse(set(self._ids(first)) & set(self._ids(second)))

    def test_db_timeout_error_is_classified_and_falls_back(self):
        from django.db import OperationalError

        err = OperationalError("canceling statement due to statement timeout")
        self.assertTrue(feed_resilience.is_timeout_error(err))
        with mock.patch.object(feed_mix, "build_pool_ids", side_effect=err):
            r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(feed_ops.summarize_last(5)["events"]["pool_timeout"], 1)

    def test_degraded_answer_counts_as_available_but_degraded(self):
        with mock.patch.object(feed_mix, "build_pool_ids", side_effect=RuntimeError("boom")):
            self.client.get(self.url)
        s = feed_ops.summarize_last(5)
        self.assertEqual((s["requests"], s["errors"], s["degraded"]), (1, 0, 1))
        self.assertEqual(s["availability_pct"], 100.0)
        self.assertEqual(s["events"]["pool_error"], 1)

    def test_healthy_feed_is_not_flagged_degraded(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(feed_resilience.DEGRADED_HEADER, r)
        self.assertEqual(feed_ops.summarize_last(5)["degraded"], 0)

    def test_everything_failing_gives_empty_200_counted_as_error(self):
        with mock.patch.object(feed_mix, "build_pool_ids", side_effect=RuntimeError("boom")), \
                mock.patch("post.views.HomeFeedView._serialize_slices", side_effect=RuntimeError("serializer too")):
            r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, {"count": 0, "next": None, "previous": None, "results": []})
        s = feed_ops.summarize_last(5)
        self.assertEqual(s["errors"], 1)
        self.assertEqual(s["events"]["fallback_failed"], 1)

    def test_bad_cursor_is_still_404_not_swallowed(self):
        r = self.client.get(self.url, {"cursor": "garbage"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(feed_ops.summarize_last(5)["errors"], 0)  # a client error is not our error

    def test_resilience_off_lets_errors_propagate(self):
        with override_settings(FEED_RESILIENCE={"enabled": False}), \
                mock.patch.object(feed_mix, "build_pool_ids", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.client.get(self.url)

    def test_fallback_pools_unit_never_raises(self):
        meta = {}
        with mock.patch.object(Post.objects, "filter", side_effect=RuntimeError("db gone")):
            pools = feed_resilience.fallback_pools(self.me, Post.objects, {self.friend.id}, meta)
        self.assertEqual(pools, {src: [] for src in feed_mix.SOURCES})
        self.assertTrue(meta["fallback_failed"])

    # ---- redis snapshot down: page 1 (and 2) still work ----
    def test_redis_down_page_one_still_served(self):
        with redis_down():
            r = self.client.get(self.url, {"page_size": 10})
            self.assertEqual(r.status_code, 200)
            self.assertNotIn(feed_resilience.DEGRADED_HEADER, r)  # real ranking, just not frozen
            self.assertEqual(len(r.data["results"]), 10)
            self.assertIsNotNone(r.data["next"])
            self.assertGreaterEqual(feed_ops.summarize_last(5)["events"]["snapshot_store_down"], 1)

    def test_redis_down_next_page_uses_miss_path(self):
        with redis_down():
            first = self.client.get(self.url, {"page_size": 8})
            ids = self._walk(first)
        self.assertEqual(len(ids), len(set(ids)), "rebuilt pages stay consistent enough: no duplicates")
        self.assertGreater(len(ids), 8)

    def test_redis_down_is_an_alert(self):
        with redis_down():
            self.client.get(self.url)
            with self.assertLogs("post.feed_ops", level="ERROR") as logs:
                summary = feed_ops.check_and_alert(window_minutes=15, min_requests=1)
        self.assertTrue(any("snapshot store" in p for p in summary["problems"]))
        self.assertTrue(any("FEED SLO ALERT" in line for line in logs.output))

    # ---- throttle ----
    def test_explicit_throttle_scope_limits_the_feed(self):
        self.assertEqual(
            [c.__name__ for c in reverse_throttles()], ["FeedHomeThrottle"],
            "HomeFeedView uses ONLY its own scope, not the global user/anon defaults",
        )
        with mock.patch.dict(feed_resilience.FeedHomeThrottle.THROTTLE_RATES, {"feed_home": "3/min"}):
            codes = [self.client.get(self.url).status_code for _ in range(5)]
        self.assertEqual(codes, [200, 200, 200, 429, 429])

    def test_throttle_fails_open_when_cache_is_down(self):
        with redis_down():
            codes = [self.client.get(self.url).status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 200])
        self.assertGreaterEqual(feed_ops.summarize_last(5)["events"]["throttle_cache_down"], 3)

    def test_throttle_is_per_user(self):
        other = User.objects.create_user(username="other", password="x")
        with mock.patch.dict(feed_resilience.FeedHomeThrottle.THROTTLE_RATES, {"feed_home": "1/min"}):
            self.assertEqual(self.client.get(self.url).status_code, 200)
            self.assertEqual(self.client.get(self.url).status_code, 429)
            self.client.force_authenticate(other)
            self.assertEqual(self.client.get(self.url).status_code, 200)

    # ---- stampede lock is wired into page 1 ----
    def test_page_one_goes_through_single_flight(self):
        calls = []
        real = feed_resilience.single_flight

        def spy(lock_key, probe, build):
            calls.append(lock_key)
            return real(lock_key, probe, build)

        with mock.patch.object(feed_resilience, "single_flight", side_effect=spy):
            self.client.get(self.url)
            self.client.get(self.url, {"refresh": "1"})  # pull-to-refresh must rebuild, not wait
        self.assertEqual(len(calls), 1)
        self.assertIn(f"feed:lock:cand:{self.me.pk}", calls[0])

    def test_cursor_rebuild_goes_through_single_flight_per_snapshot(self):
        first = self.client.get(self.url, {"page_size": 5})
        cache.clear()  # snapshot gone -> miss path
        calls = []
        real = feed_resilience.single_flight

        def spy(lock_key, probe, build):
            calls.append(lock_key)
            return real(lock_key, probe, build)

        with mock.patch.object(feed_resilience, "single_flight", side_effect=spy):
            r = self.client.get(first.data["next"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertIn(":snap:", calls[0])


def reverse_throttles():
    from .views import HomeFeedView

    return HomeFeedView.throttle_classes


# ---------------------------------------------------------------- single-flight (pure)
@override_settings(FEED_RESILIENCE={"lock_wait_seconds": 0.3, "lock_poll_ms": 10, "lock_ttl_seconds": 5})
class SingleFlightTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        feed_ops._local.clear()

    def test_free_lock_builds_once_and_releases(self):
        built = []
        out = feed_resilience.single_flight("lk:1", lambda: None, lambda: built.append(1) or "fresh")
        self.assertEqual((out, built), ("fresh", [1]))
        self.assertIsNone(cache.get("lk:1"), "lock released")

    def test_probe_after_lock_avoids_second_build(self):
        built = []
        out = feed_resilience.single_flight("lk:2", lambda: "racer-finished", lambda: built.append(1))
        self.assertEqual(out, "racer-finished")
        self.assertEqual(built, [])

    def test_follower_waits_for_the_builder_instead_of_building(self):
        cache.add("lk:3", "someone-else", 5)  # another worker is building
        state = {"polls": 0}

        def probe():
            state["polls"] += 1
            return "from-cache" if state["polls"] >= 3 else None

        built = []
        out = feed_resilience.single_flight("lk:3", probe, lambda: built.append(1))
        self.assertEqual(out, "from-cache")
        self.assertEqual(built, [], "the follower did NOT rebuild the pools")
        self.assertEqual(cache.get("lk:3"), "someone-else", "a follower never frees the builder's lock")

    def test_follower_builds_itself_when_builder_is_too_slow(self):
        cache.add("lk:4", "stuck-worker", 5)
        out = feed_resilience.single_flight("lk:4", lambda: None, lambda: "built-by-me")
        self.assertEqual(out, "built-by-me")
        self.assertEqual(feed_ops.summarize_last(5)["events"]["lock_wait_timeout"], 1)

    def test_lock_released_even_when_build_raises(self):
        with self.assertRaises(RuntimeError):
            feed_resilience.single_flight("lk:5", lambda: None, mock.Mock(side_effect=RuntimeError("x")))
        self.assertIsNone(cache.get("lk:5"))

    def test_cache_down_just_builds(self):
        with redis_down():
            out = feed_resilience.single_flight("lk:6", lambda: None, lambda: "built")
        self.assertEqual(out, "built")

    def test_lock_disabled_just_builds(self):
        with override_settings(FEED_RESILIENCE={"lock_enabled": False}):
            cache.add("lk:7", "x", 5)
            out = feed_resilience.single_flight("lk:7", lambda: "never", lambda: "built")
        self.assertEqual(out, "built")


# ---------------------------------------------------------------- SLO numbers
class FeedOpsTests(SimpleTestCase):
    def setUp(self):
        _reset_ops()

    def test_empty_window_is_not_a_breach(self):
        s = feed_ops.summarize_last(15)
        self.assertEqual(s["requests"], 0)
        self.assertEqual(feed_ops.breaches(s), [])

    def test_availability_counts_only_errors(self):
        for _ in range(197):
            feed_ops.record_request(100, feed_ops.OUTCOME_OK)
        for _ in range(2):
            feed_ops.record_request(100, feed_ops.OUTCOME_DEGRADED)
        feed_ops.record_request(100, feed_ops.OUTCOME_ERROR)
        s = feed_ops.summarize_last(15)
        self.assertEqual((s["requests"], s["errors"], s["degraded"]), (200, 1, 2))
        self.assertEqual(s["availability_pct"], 99.5)
        self.assertTrue(s["slo"]["availability_ok"])  # exactly at target = ok

    def test_availability_breach(self):
        for _ in range(98):
            feed_ops.record_request(100)
        for _ in range(2):
            feed_ops.record_request(100, feed_ops.OUTCOME_ERROR)
        problems = feed_ops.breaches(feed_ops.summarize_last(15))
        self.assertEqual(len(problems), 1)
        self.assertIn("availability", problems[0])

    def test_p95_within_target(self):
        for _ in range(96):
            feed_ops.record_request(300)
        for _ in range(4):
            feed_ops.record_request(1500)
        s = feed_ops.summarize_last(15)
        self.assertEqual(s["p95_ms"], 400)
        self.assertTrue(s["slo"]["p95_ok"])

    def test_p95_breach(self):
        for _ in range(90):
            feed_ops.record_request(300)
        for _ in range(10):
            feed_ops.record_request(1500)
        s = feed_ops.summarize_last(15)
        self.assertEqual(s["p95_ms"], 1600)
        self.assertFalse(s["slo"]["p95_ok"])
        self.assertTrue(any("p95" in p for p in feed_ops.breaches(s)))

    def test_alert_needs_enough_traffic_and_logs_once(self):
        feed_ops.record_request(100, feed_ops.OUTCOME_ERROR)
        s = feed_ops.check_and_alert(min_requests=50)  # 1 request: too small a sample to page anyone
        self.assertEqual(s["problems"], [])
        for _ in range(60):
            feed_ops.record_request(100, feed_ops.OUTCOME_ERROR)
        with self.assertLogs("post.feed_ops", level="ERROR") as logs:
            s = feed_ops.check_and_alert(min_requests=50)
        self.assertTrue(s["problems"])
        self.assertEqual(len(logs.output), 1)

    def test_counters_survive_a_dead_cache_in_process(self):
        with redis_down():
            feed_ops.record_request(100)
            feed_ops.note("pool_error")
            s = feed_ops.summarize_last(5)
        self.assertEqual(s["requests"], 1)
        self.assertEqual(s["events"]["pool_error"], 1)

    def test_prometheus_text(self):
        feed_ops.record_request(100)
        text = feed_ops.prometheus_text()
        for name in ("learnscroll_feed_requests", "learnscroll_feed_availability_pct",
                     "learnscroll_feed_p95_ms", "learnscroll_feed_events"):
            self.assertIn(f"# TYPE {name} gauge", text)
        self.assertIn('learnscroll_feed_requests{window="5m"} 1', text)


class FeedOpsEndpointTests(APITestCase):
    def setUp(self):
        _reset_ops()
        self.url = reverse("post-feed-ops")
        self.staff = User.objects.create_user(username="staff", password="x", is_staff=True)
        self.user = User.objects.create_user(username="plain", password="x")

    def test_anonymous_and_plain_user_are_refused(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.url, HTTP_AUTHORIZATION="Bearer nope").status_code, 403)

    @override_settings(FEED_OPS_SCRAPE_TOKEN="s3cret-token")
    def test_scrape_token_gets_prometheus_text(self):
        feed_ops.record_request(120)
        r = self.client.get(self.url, {"format": "prom"}, HTTP_AUTHORIZATION="Bearer s3cret-token")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/plain", r["Content-Type"])
        self.assertIn("learnscroll_feed_availability_pct", r.content.decode())

    @override_settings(FEED_OPS_SCRAPE_TOKEN="")
    def test_empty_token_setting_disables_token_access(self):
        self.assertEqual(self.client.get(self.url, HTTP_AUTHORIZATION="Bearer ").status_code, 403)

    @override_settings(FEED_OPS_SCRAPE_TOKEN="s3cret-token")
    def test_wrong_token_is_refused(self):
        self.assertEqual(self.client.get(self.url, HTTP_AUTHORIZATION="Bearer s3cret-tokeX").status_code, 403)

    def test_staff_jwt_gets_json(self):
        from rest_framework_simplejwt.tokens import AccessToken

        token = str(AccessToken.for_user(self.staff))
        feed_ops.record_request(120)
        r = self.client.get(self.url, {"minutes": 15}, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["data"]["requests"], 1)
        self.assertIn("breaches", r.data["data"])


class DailyMetricsOpsTests(TestCase):
    def setUp(self):
        _reset_ops()

    def test_daily_job_includes_ops_block(self):
        from . import tasks

        feed_ops.record_request(100)
        today = timezone.now().date().isoformat()
        summary = tasks.feed_daily_metrics(date_str=today)
        self.assertIn("ops", summary)
        self.assertEqual(summary["ops"]["requests"], 1)
        self.assertIn("breaches", summary["ops"])

    def test_slo_watch_task_returns_summary(self):
        from . import tasks

        feed_ops.record_request(100)
        out = tasks.feed_slo_watch()
        self.assertEqual(out["requests"], 1)
        self.assertEqual(out["problems"], [])


# ---------------------------------------------------------------- retention
class FeedRetentionTests(TestCase):
    def setUp(self):
        self.u1 = User.objects.create_user(username="r1", password="x")
        self.u2 = User.objects.create_user(username="r2", password="x")
        self.author = User.objects.create_user(username="author", password="x")
        self.posts = [_mk_post(self.author, f"p{i}") for i in range(8)]

    def _view(self, post, days_old, user=None, **kw):
        v = PostView.objects.create(post=post, user=user or self.u1, **kw)
        PostView.objects.filter(pk=v.pk).update(viewed_at=timezone.now() - datetime.timedelta(days=days_old))
        return v

    def _event(self, post, days_old):
        e = PostEvent.objects.create(user=self.u1, post=post, event_type="impression", surface="feed")
        PostEvent.objects.filter(pk=e.pk).update(created_at=timezone.now() - datetime.timedelta(days=days_old))
        return e

    def test_post_events_older_than_retention_are_deleted(self):
        old = [self._event(self.posts[0], 45), self._event(self.posts[1], 31)]
        keep = [self._event(self.posts[2], 29), self._event(self.posts[3], 0)]
        out = feed_retention.prune_post_events()
        self.assertEqual(out["rows"], 2)
        self.assertTrue(out["complete"])
        self.assertFalse(PostEvent.objects.filter(pk__in=[e.pk for e in old]).exists())
        self.assertEqual(PostEvent.objects.filter(pk__in=[e.pk for e in keep]).count(), 2)

    def test_prune_batches_through_a_backlog(self):
        for _ in range(7):
            self._event(self.posts[0], 60)
        with override_settings(FEED_RETENTION={"batch_size": 100, "pause_ms": 0}):
            out = feed_retention.prune_post_events(batch_size=3)
        self.assertEqual(out["rows"], 7)
        self.assertEqual(PostEvent.objects.count(), 0)

    def test_time_budget_stops_the_run_and_reports_incomplete(self):
        for _ in range(6):
            self._event(self.posts[0], 60)
        with override_settings(FEED_RETENTION={"max_seconds": 0, "pause_ms": 0}):
            out = feed_retention.prune_post_events(batch_size=2)
        self.assertFalse(out["complete"])
        self.assertEqual(out["rows"], 2)  # one batch, then it hands the rest to the next run
        self.assertEqual(PostEvent.objects.count(), 4)

    def test_dry_run_deletes_nothing(self):
        self._event(self.posts[0], 90)
        out = feed_retention.prune_post_events(dry_run=True)
        self.assertEqual((out["rows"], PostEvent.objects.count()), (1, 1))

    def test_task_keeps_its_old_contract(self):
        from . import tasks

        self._event(self.posts[0], 90)
        self._event(self.posts[1], 1)
        self.assertEqual(tasks.prune_old_post_events(), 1)

    def test_seen_rows_pruned_but_counted_and_watch_rows_kept(self):
        old_seen = self._view(self.posts[0], 100, is_counted=False)
        old_counted = self._view(self.posts[1], 100, is_counted=True)  # de-dupes views_count: never pruned
        old_watched = self._view(self.posts[2], 100, is_counted=False, watch_seconds=12.0)
        young_seen = self._view(self.posts[3], 20, is_counted=False)
        mid_seen = self._view(self.posts[4], 40, is_counted=False)  # past the 30 d window, inside the 60 d keep
        out = feed_retention.prune_seen_post_views()
        self.assertEqual(out["rows"], 1)
        remaining = set(PostView.objects.values_list("pk", flat=True))
        self.assertNotIn(old_seen.pk, remaining)
        self.assertTrue({old_counted.pk, old_watched.pk, young_seen.pk, mid_seen.pk} <= remaining)

    def test_seen_retention_never_drops_below_the_feed_seen_window(self):
        # misconfigured to 5 days while the feed still treats the last 30 days as "seen"
        with override_settings(FEED_RETENTION={"seen_days": 5}, FEED_SEEN_LIMITS={"window_days": 30}):
            self.assertEqual(feed_retention.effective_seen_days(), 37)
            v = self._view(self.posts[0], 33, is_counted=False)  # still inside window + margin
            feed_retention.prune_seen_post_views(days=1)  # an explicit smaller `days` is clamped too
        self.assertTrue(PostView.objects.filter(pk=v.pk).exists())

    def test_pruned_seen_rows_do_not_change_the_feed_seen_set(self):
        self._view(self.posts[0], 100, is_counted=False)  # outside the 30 d window anyway
        recent = self._view(self.posts[1], 3, is_counted=False)
        before = feed_mix.get_seen_post_ids(self.u1)
        feed_retention.prune_seen_post_views()
        self.assertEqual(feed_mix.get_seen_post_ids(self.u1), before)
        self.assertIn(recent.post_id, before)

    def test_pii_scrub_blanks_ip_and_user_agent_but_keeps_the_row(self):
        old = self._view(self.posts[0], 120, is_counted=True, ip_address="203.0.113.9", user_agent="UA/1.0")
        new = self._view(self.posts[1], 5, is_counted=True, ip_address="203.0.113.10", user_agent="UA/2.0")
        out = feed_retention.scrub_post_view_pii()
        self.assertEqual(out["rows"], 1)
        old.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual((old.ip_address, old.user_agent, old.is_counted), (None, None, True))
        self.assertEqual((new.ip_address, new.user_agent), ("203.0.113.10", "UA/2.0"))

    def test_run_all_isolates_failures(self):
        with mock.patch.object(feed_retention, "prune_seen_post_views", side_effect=RuntimeError("db hiccup")):
            res = feed_retention.run_all()
        self.assertIn("error", res["seen_post_views"])
        self.assertIn("rows", res["post_events"])
        self.assertIn("rows", res["post_view_pii"])

    def test_management_command_dry_run(self):
        from io import StringIO

        from django.core.management import call_command

        self._event(self.posts[0], 90)
        out = StringIO()
        call_command("feed_retention", "--dry-run", stdout=out)
        self.assertIn("would touch", out.getvalue())
        self.assertEqual(PostEvent.objects.count(), 1)
