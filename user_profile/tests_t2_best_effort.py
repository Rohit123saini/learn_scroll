"""
T2.3 - heartbeat and streak are "best effort": a lock that never clears answers 200 {ok: false}, never 500.
(written by reading the code; NOT run yet)  Run: python manage.py test user_profile.tests_t2_best_effort
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.urls import reverse
from rest_framework.test import APITestCase

User = get_user_model()


class BestEffortViewsTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="t2_user", email="t2_user@example.com", password="testpass123")
        self.client.force_authenticate(self.user)

    @mock.patch("common.db_retry.time.sleep")
    def test_heartbeat_locked_forever_is_200_ok_false(self, _sleep):
        with mock.patch(
            "user_profile.views.DailyUsage.objects.record_heartbeat",
            side_effect=OperationalError("database is locked"),
        ):
            res = self.client.post(reverse("activity-heartbeat"), {"seconds": 30}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIs(res.data["ok"], False)

    def test_heartbeat_normal_path_has_ok_true(self):
        res = self.client.post(reverse("activity-heartbeat"), {"seconds": 5}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIs(res.data["ok"], True)

    @mock.patch("common.db_retry.time.sleep")
    def test_streak_locked_forever_is_200_ok_false(self, _sleep):
        with mock.patch(
            "user_profile.views.Streak.objects.record_activity",
            side_effect=OperationalError("database is locked"),
        ):
            res = self.client.post(reverse("streak"))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIs(res.data["ok"], False)

    def test_streak_normal_path_still_works(self):
        res = self.client.post(reverse("streak"))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["status"])

    @mock.patch("common.db_retry.time.sleep")
    def test_streak_retries_inside_the_model_then_succeeds(self, _sleep):
        from user_profile.models import Streak

        real = Streak.objects._record_activity_once
        calls = {"n": 0}

        def flaky(user):
            calls["n"] += 1
            if calls["n"] < 3:
                raise OperationalError("database is locked")
            return real(user)

        with mock.patch.object(Streak.objects, "_record_activity_once", side_effect=flaky):
            streak, _hit = Streak.objects.record_activity(self.user)
        self.assertEqual(calls["n"], 3)
        self.assertEqual(streak.current_streak, 1)
