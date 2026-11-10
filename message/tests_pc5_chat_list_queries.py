# message/tests_pc5_chat_list_queries.py
# P-C5: GET /message/conversations/ must not run one query per private chat (other participant is prefetched).
# NOT RUN when written. Run: python manage.py test message.tests_pc5_chat_list_queries -v 2
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import Conversation

User = get_user_model()
GROWTH_SLACK = 3


class ChatListQueryTests(APITestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="pc5_chat_me", password="x")
        self.client.force_authenticate(self.me)
        self.n = 0

    def _add_private_chats(self, count):
        for _ in range(count):
            self.n += 1
            friend = User.objects.create_user(username=f"pc5_chat_f{self.n}", password="x")
            Conversation.get_or_create_private(self.me, friend)

    def _list(self):
        with CaptureQueriesContext(connection) as ctx:
            r = self.client.get(reverse("conversation-list"))
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        return len(ctx), r

    def test_query_count_does_not_grow_with_private_chats(self):
        self._add_private_chats(2)
        small, _ = self._list()
        self._add_private_chats(10)
        large, r = self._list()
        self.assertLessEqual(large, small + GROWTH_SLACK, f"2 chats = {small} queries, 12 chats = {large}")
        rows = r.data.get("results", r.data)
        self.assertEqual(len(rows), 12)

    def test_other_participant_is_the_friend_not_me(self):
        self._add_private_chats(3)
        _, r = self._list()
        for row in r.data.get("results", r.data):
            other = row["other_participant"]
            self.assertIsNotNone(other)
            self.assertNotEqual(str(other["id"]), str(self.me.id))
