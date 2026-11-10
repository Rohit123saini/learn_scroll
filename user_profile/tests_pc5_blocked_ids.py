# user_profile/tests_pc5_blocked_ids.py
# P-C5: blocked_user_ids() is ONE query (was two) and returns the same set as before.
# NOT RUN when written. Run: python manage.py test user_profile.tests_pc5_blocked_ids -v 2
from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import BlockUser
from .services import blocked_user_ids

User = get_user_model()


class BlockedUserIdsTests(TestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="pc5_b_me", password="x")
        self.i_blocked = User.objects.create_user(username="pc5_b_a", password="x")
        self.blocked_me = User.objects.create_user(username="pc5_b_b", password="x")
        self.unrelated = User.objects.create_user(username="pc5_b_c", password="x")
        BlockUser.objects.create(blocker=self.me, blocked=self.i_blocked)
        BlockUser.objects.create(blocker=self.blocked_me, blocked=self.me)
        BlockUser.objects.create(blocker=self.i_blocked, blocked=self.unrelated)  # not mine: must not leak in

    def test_both_directions_and_nothing_else(self):
        self.assertEqual(blocked_user_ids(self.me), {self.i_blocked.id, self.blocked_me.id})

    def test_accepts_a_raw_id(self):
        self.assertEqual(blocked_user_ids(self.me.id), {self.i_blocked.id, self.blocked_me.id})

    def test_single_query(self):
        with self.assertNumQueries(1):
            blocked_user_ids(self.me)

    def test_user_without_blocks_is_empty(self):
        self.assertEqual(blocked_user_ids(self.unrelated), {self.i_blocked.id})  # i_blocked blocked unrelated
        lone = User.objects.create_user(username="pc5_b_lone", password="x")
        self.assertEqual(blocked_user_ids(lone), set())
