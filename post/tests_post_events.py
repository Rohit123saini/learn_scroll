# post/tests_post_events.py — C1-BE: PostEvent analytics log,
# POST /post/events/ (bulk, max 100, throttled) and the daily prune task.
import uuid
from datetime import timedelta
from unittest import mock, skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.throttling import ScopedRateThrottle

from .models import Post, PostEvent
from .tasks import prune_old_post_events

User = get_user_model()

# The real project may point CACHES at Redis; throttle counters must live in
# an isolated in-process cache so tests are hermetic and can't 429 each other.
_LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "post-events-tests"}}


def _ev(post_id, event_type="impression", surface="feed", **extra):
    d = {"post_id": str(post_id), "event_type": event_type, "surface": surface}
    d.update(extra)
    return d


@override_settings(CACHES=_LOCMEM)
class PostEventBulkTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.author = User.objects.create_user(username="author", password="x")
        self.posts = [Post.objects.create(user=self.author, content=f"p{i}") for i in range(3)]
        self.url = reverse("post-events")
        self.client.force_authenticate(self.me)

    def _send(self, events):
        return self.client.post(self.url, {"events": events}, format="json")

    def test_requires_auth(self):
        self.client.force_authenticate(None)
        self.assertEqual(self._send([_ev(self.posts[0].id)]).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_stores_valid_events(self):
        r = self._send([
            _ev(self.posts[0].id, "impression", "feed"),
            _ev(self.posts[0].id, "dwell", "reels", dwell_ms=4200),
            _ev(self.posts[1].id, "tap", "profile"),
            _ev(self.posts[2].id, "skip", "explore", dwell_ms=300),
        ])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["accepted"], 4)
        self.assertEqual(r.data["ignored"], 0)
        self.assertEqual(PostEvent.objects.filter(user=self.me).count(), 4)
        dwell = PostEvent.objects.get(event_type="dwell")
        self.assertEqual((dwell.post_id, dwell.surface, dwell.dwell_ms), (self.posts[0].id, "reels", 4200))
        self.assertEqual(PostEvent.objects.get(event_type="skip").dwell_ms, 300)
        self.assertIsNotNone(dwell.created_at)

    def test_impression_and_tap_dwell_forced_to_zero(self):
        self._send([_ev(self.posts[0].id, "impression", dwell_ms=999), _ev(self.posts[0].id, "tap", dwell_ms=999)])
        self.assertFalse(PostEvent.objects.exclude(dwell_ms=0).exists())

    def test_same_event_can_repeat(self):
        # Append-only log: no dedupe on (user, post, type).
        self._send([_ev(self.posts[0].id)] * 3)
        self.assertEqual(PostEvent.objects.count(), 3)

    def test_events_are_recorded_for_the_caller_only(self):
        self._send([_ev(self.posts[0].id)])
        self.assertEqual(PostEvent.objects.get().user_id, self.me.id)

    def test_exactly_100_ok(self):
        r = self._send([_ev(self.posts[0].id) for _ in range(100)])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["accepted"], 100)
        self.assertEqual(PostEvent.objects.count(), 100)

    def test_over_100_is_400_and_writes_nothing(self):
        r = self._send([_ev(self.posts[0].id) for _ in range(101)])
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(PostEvent.objects.count(), 0)
        r = self._send([_ev(self.posts[0].id) for _ in range(250)])
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malformed_payloads_400(self):
        pid = self.posts[0].id
        bad = [
            None,
            "nope",
            {"a": 1},
            [1],
            [_ev("not-a-uuid")],
            [{"event_type": "tap", "surface": "feed"}],                 # missing post_id
            [_ev(pid, "hover")],                                         # bad event_type
            [_ev(pid, "tap", "story")],                                  # bad surface
            [_ev(pid, "dwell")],                                         # dwell needs dwell_ms
            [_ev(pid, "dwell", dwell_ms=-1)],
            [_ev(pid, "dwell", dwell_ms="lots")],
            [_ev(pid, "dwell", dwell_ms=True)],
            [_ev(pid, "dwell", dwell_ms=60 * 60 * 1000 + 1)],
        ]
        for events in bad:
            r = self.client.post(self.url, {"events": events}, format="json")
            self.assertEqual(r.status_code, 400, msg=f"expected 400 for {events!r}")
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 400)
        self.assertEqual(PostEvent.objects.count(), 0)

    def test_one_bad_event_rejects_whole_batch(self):
        r = self._send([_ev(self.posts[0].id), _ev(self.posts[1].id), _ev(self.posts[2].id, "hover")])
        self.assertEqual(r.status_code, 400)
        self.assertIn("events[2]", r.data["message"])
        self.assertEqual(PostEvent.objects.count(), 0)

    def test_empty_list_ok(self):
        r = self._send([])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["accepted"], 0)

    def test_unknown_and_deleted_posts_ignored(self):
        gone = self.posts[2]
        gone.is_deleted = True
        gone.save(update_fields=["is_deleted"])
        r = self._send([_ev(self.posts[0].id), _ev(uuid.uuid4()), _ev(gone.id)])
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.data["accepted"], r.data["ignored"]), (1, 2))
        self.assertEqual(PostEvent.objects.count(), 1)

    def test_does_not_touch_views_or_seen_state(self):
        from .models import PostView
        self._send([_ev(self.posts[0].id, "dwell", dwell_ms=1000)])
        self.posts[0].refresh_from_db()
        self.assertEqual(self.posts[0].views_count, 0)
        self.assertFalse(PostView.objects.exists())


@override_settings(CACHES=_LOCMEM)
class PostEventThrottleTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.other = User.objects.create_user(username="other", password="x")
        author = User.objects.create_user(username="author", password="x")
        self.post = Post.objects.create(user=author, content="p")
        self.url = reverse("post-events")

    def _send(self):
        return self.client.post(self.url, {"events": [_ev(self.post.id)]}, format="json")

    def test_scope_has_a_rate_in_settings(self):
        # Without this key ScopedRateThrottle raises ImproperlyConfigured (500).
        self.assertIn("post_events", settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])

    def test_throttled_after_rate_exceeded(self):
        rates = {**ScopedRateThrottle.THROTTLE_RATES, "post_events": "3/min"}
        with mock.patch.object(ScopedRateThrottle, "THROTTLE_RATES", rates):
            self.client.force_authenticate(self.me)
            for _ in range(3):
                self.assertEqual(self._send().status_code, 200)
            r = self._send()
            self.assertEqual(r.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
            self.assertEqual(PostEvent.objects.count(), 3)  # the throttled call wrote nothing
            # per-user bucket: someone else is unaffected
            self.client.force_authenticate(self.other)
            self.assertEqual(self._send().status_code, 200)

    def test_real_configured_rate_allows_normal_traffic(self):
        self.client.force_authenticate(self.me)
        for _ in range(5):
            self.assertEqual(self._send().status_code, 200)


class PrunePostEventsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", password="x")
        self.post = Post.objects.create(user=self.user, content="p")

    def _make(self, age_days, n=1):
        ids = []
        for _ in range(n):
            e = PostEvent.objects.create(user=self.user, post=self.post, event_type="impression", surface="feed")
            ids.append(e.pk)
        # created_at is auto_now_add, so backdate with a queryset update.
        PostEvent.objects.filter(pk__in=ids).update(created_at=timezone.now() - timedelta(days=age_days))
        return ids

    def test_deletes_only_rows_older_than_30_days(self):
        old = self._make(31, 3)
        edge_old = self._make(45, 1)
        fresh = self._make(29, 2)
        today = self._make(0, 1)
        deleted = prune_old_post_events()
        self.assertEqual(deleted, 4)
        self.assertFalse(PostEvent.objects.filter(pk__in=old + edge_old).exists())
        self.assertEqual(PostEvent.objects.filter(pk__in=fresh + today).count(), 3)

    def test_noop_when_nothing_old(self):
        self._make(1, 2)
        self.assertEqual(prune_old_post_events(), 0)
        self.assertEqual(PostEvent.objects.count(), 2)

    def test_custom_retention_days(self):
        self._make(8, 2)
        self._make(3, 1)
        self.assertEqual(prune_old_post_events(days=7), 2)
        self.assertEqual(PostEvent.objects.count(), 1)

    def test_batches_delete_everything(self):
        self._make(60, 7)
        self._make(1, 1)
        self.assertEqual(prune_old_post_events(batch_size=2), 7)  # 4 batches
        self.assertEqual(PostEvent.objects.count(), 1)

    def test_runs_as_a_celery_task_call(self):
        self._make(40, 2)
        self.assertEqual(prune_old_post_events.apply().get(), 2)

    @skipUnless(hasattr(settings, "CELERY_BEAT_SCHEDULE"), "full project settings only")
    def test_is_scheduled_daily_in_beat(self):
        entry = settings.CELERY_BEAT_SCHEDULE["post-prune-old-events"]
        self.assertEqual(entry["task"], "post.tasks.prune_old_post_events")