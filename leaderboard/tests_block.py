"""Audit item 15: a blocked user (either direction) must not show up on my leaderboard."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from leaderboard.models import LeaderboardEntry
from user_profile.models import BlockUser

User = get_user_model()


class LeaderboardBlockTests(TestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="pw12345678")
        self.a = User.objects.create_user(username="a", password="pw12345678")
        self.b = User.objects.create_user(username="b", password="pw12345678")
        for rank, u in enumerate([self.a, self.me, self.b], start=1):
            LeaderboardEntry.objects.create(
                scope_type="engagement", scope_id=None, period_type="all_time", period_key="all",
                user=u, score=100 - rank, rank=rank,
            )
        self.c = APIClient()
        self.c.force_authenticate(self.me)

    def names(self):
        cache.clear()
        data = self.c.get("/leaderboard/board/", {"scope_type": "engagement", "period": "all_time"}).json()
        rows = data.get("results") or data.get("data", {}).get("results", [])
        return {r.get("username") or r.get("user", {}).get("username") for r in rows}

    def test_everyone_visible_without_blocks(self):
        self.assertEqual(self.names(), {"a", "me", "b"})

    def test_i_blocked_them(self):
        BlockUser.objects.create(blocker=self.me, blocked=self.a)
        self.assertNotIn("a", self.names())

    def test_they_blocked_me(self):
        BlockUser.objects.create(blocker=self.b, blocked=self.me)
        names = self.names()
        self.assertNotIn("b", names)
        self.assertIn("me", names)  # I still see myself
