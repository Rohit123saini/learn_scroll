"""Run:  python manage.py test message.tests_directory_search

Friends-of-friends in both people-search endpoints:
  * GET /message/search/directory/?q=      (message search screen)
  * GET /profile/chat-search/?search=      (new chat / add group members)
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from user_profile.models import BlockUser, Follow

User = get_user_model()


def _u(name, **kw):
    return User.objects.create_user(username=name, password="pw12345!", **kw)


def _follow(a, b):
    Follow.objects.create(follower=a, following=b, status=Follow.Status.ACCEPTED)


class DirectoryFriendOfFriendTests(TestCase):
    def setUp(self):
        self.me = _u("zz_me")
        self.friend = _u("zz_friend")      # I follow him
        self.fof = _u("zz_fof_pub")        # friend follows him -> friend-of-friend
        self.stranger = _u("zz_stranger")  # no link at all
        _follow(self.me, self.friend)
        _follow(self.friend, self.fof)
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def _people(self, q="zz_"):
        res = self.client.get("/message/search/directory/", {"q": q, "type": "people"})
        self.assertEqual(res.status_code, 200)
        return {p["username"]: p for p in res.data["people"]}

    def test_followed_user_and_fof_are_found_stranger_is_not(self):
        people = self._people()
        self.assertIn("zz_friend", people)
        self.assertEqual(people["zz_friend"]["relation"], "following")
        self.assertIn("zz_fof_pub", people)
        self.assertEqual(people["zz_fof_pub"]["relation"], "friend_of_friend")
        self.assertNotIn("zz_stranger", people)

    def test_direct_connections_rank_before_fof(self):
        res = self.client.get("/message/search/directory/", {"q": "zz_", "type": "people"})
        names = [p["username"] for p in res.data["people"]]
        self.assertLess(names.index("zz_friend"), names.index("zz_fof_pub"))

    def test_private_fof_is_hidden(self):
        priv = _u("zz_fof_priv", is_private=True)
        _follow(self.friend, priv)
        self.assertNotIn("zz_fof_priv", self._people())

    def test_blocked_fof_is_hidden(self):
        BlockUser.objects.create(blocker=self.me, blocked=self.fof)
        self.assertNotIn("zz_fof_pub", self._people())

    def test_pending_follow_does_not_create_fof(self):
        other = _u("zz_pending_fof")
        Follow.objects.create(follower=self.friend, following=other, status=Follow.Status.PENDING)
        self.assertNotIn("zz_pending_fof", self._people())


class ChatSearchFriendOfFriendTests(TestCase):
    def setUp(self):
        self.me = _u("yy_me")
        self.friend = _u("yy_friend")
        self.fof = _u("yy_fof")
        self.stranger = _u("yy_stranger")
        _follow(self.me, self.friend)
        _follow(self.fof, self.friend)  # fof follows my friend (other direction)
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def _rows(self, q="yy_"):
        res = self.client.get("/profile/chat-search/", {"search": q})
        self.assertEqual(res.status_code, 200)
        data = res.data["data"]
        rows = data["results"] if isinstance(data, dict) else data
        return {r["username"]: r for r in rows}

    def test_followed_and_fof_found_stranger_not(self):
        rows = self._rows()
        self.assertIn("yy_friend", rows)
        self.assertFalse(rows["yy_friend"]["is_friend_of_friend"])
        self.assertIn("yy_fof", rows)
        self.assertTrue(rows["yy_fof"]["is_friend_of_friend"])
        self.assertNotIn("yy_stranger", rows)

    def test_inactive_user_hidden(self):
        User.objects.filter(pk=self.fof.pk).update(is_active=False)
        self.assertNotIn("yy_fof", self._rows())
