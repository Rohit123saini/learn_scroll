"""P-F9_F10 tests for post/creator_analytics.py (written, NOT RUN)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from . import creator_analytics as ca
from .models import Post, PostEvent

User = get_user_model()


class PureHelperTests(SimpleTestCase):
    def test_change_pct(self):
        self.assertIsNone(ca.change_pct(5, 0))
        self.assertEqual(ca.change_pct(150, 100), 50.0)
        self.assertEqual(ca.change_pct(50, 100), -50.0)

    def test_weekday_mapping_monday_is_zero(self):
        # Django ExtractWeekDay: Sunday=1, Monday=2, Saturday=7
        grid = ca.weekday_hour_grid([(2, 9, 3), (1, 23, 2), (7, 0, 1)])
        self.assertEqual(grid[0][9], 3)   # Monday
        self.assertEqual(grid[6][23], 2)  # Sunday
        self.assertEqual(grid[5][0], 1)   # Saturday

    def test_grid_ignores_bad_rows(self):
        grid = ca.weekday_hour_grid([(None, 5, 1), (2, None, 1), (2, 99, 1)])
        self.assertEqual(sum(sum(r) for r in grid), 0)

    def test_top_slots_sorted_and_skips_empty(self):
        grid = [[0] * 24 for _ in range(7)]
        grid[2][18] = 9
        grid[1][8] = 9
        grid[0][7] = 4
        slots = ca.top_slots(grid, limit=3)
        self.assertEqual([(s["weekday"], s["hour"]) for s in slots], [(1, 8), (2, 18), (0, 7)])
        self.assertEqual(ca.top_slots([[0] * 24 for _ in range(7)]), [])


class CreatorAnalyticsApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.creator = User.objects.create_user(username="creator", password="x")
        self.fan = User.objects.create_user(username="fan", password="x")
        self.post = Post.objects.create(user=self.creator, content="hello")
        self.client = APIClient()
        self.url = reverse("creator-analytics")

    def _events(self, n, user=None, kind=PostEvent.EventType.DWELL, dwell=2000):
        for _ in range(n):
            PostEvent.objects.create(
                user=user or self.fan, post=self.post, event_type=kind,
                dwell_ms=dwell, surface=PostEvent.Surface.FEED,
            )

    def test_requires_login(self):
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    def test_empty_account_returns_zeros_not_error(self):
        self.client.force_authenticate(self.creator)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["best_time"]["enough_data"])
        self.assertEqual(r.data["best_time"]["top_slots"], [])
        self.assertEqual(len(r.data["watch_time"]["daily"]), 7)
        self.assertEqual(r.data["watch_time"]["total_s"], 0.0)

    def test_bad_days_falls_back_and_clamps(self):
        self.client.force_authenticate(self.creator)
        self.assertEqual(self.client.get(self.url + "?days=abc").data["days"], 7)
        self.assertEqual(self.client.get(self.url + "?days=9999").data["days"], 90)

    def test_watch_time_counts_other_people_only(self):
        self._events(3, dwell=2000)                       # fan: 6 s
        self._events(5, user=self.creator, dwell=9000)    # own activity: ignored
        self.client.force_authenticate(self.creator)
        r = self.client.get(self.url)
        self.assertEqual(r.data["watch_time"]["total_s"], 6.0)

    def test_best_time_needs_enough_events(self):
        self._events(ca.MIN_EVENTS_FOR_BEST_TIME - 1)
        self.client.force_authenticate(self.creator)
        self.assertFalse(self.client.get(self.url).data["best_time"]["enough_data"])
        cache.clear()
        self._events(1)
        bt = self.client.get(self.url).data["best_time"]
        self.assertTrue(bt["enough_data"])
        self.assertEqual(len(bt["top_slots"]), 1)  # all events in the same hour slot (test runs fast)
        self.assertEqual(bt["top_slots"][0]["score"], ca.MIN_EVENTS_FOR_BEST_TIME)

    def test_never_leaks_other_creators_data(self):
        other = User.objects.create_user(username="other", password="x")
        self._events(30)
        self.client.force_authenticate(other)
        r = self.client.get(self.url)
        self.assertEqual(r.data["watch_time"]["total_s"], 0.0)
        self.assertFalse(r.data["best_time"]["enough_data"])

    def test_second_call_is_served_from_cache(self):
        self.client.force_authenticate(self.creator)
        first = self.client.get(self.url).data["generated_at"]
        self._events(4)
        again = self.client.get(self.url).data
        self.assertEqual(again["generated_at"], first)
        self.assertEqual(again["watch_time"]["total_s"], 0.0)  # still the cached snapshot

    def test_old_events_outside_window_not_counted(self):
        self._events(3)
        PostEvent.objects.update(created_at=timezone.now() - timedelta(days=20))
        self.client.force_authenticate(self.creator)
        self.assertEqual(self.client.get(self.url + "?days=7").data["watch_time"]["total_s"], 0.0)
