"""
post/tests_mute_scope.py  (P8 - "mute posts but keep stories" and the other way round)

Likhe gaye hain, CHALAYE NAHI gaye.  Run:
    python manage.py test post.tests_mute_scope

Old contract must still hold: POST {"user_id"} with no scope = mute everything.
"""
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from post.models import MutedAccount, Post
from post.services import exclude_hidden_and_muted

User = get_user_model()


def _user(name):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345")


class MuteScopeApiTests(APITestCase):
    def setUp(self):
        self.me = _user("ms_me")
        self.other = _user("ms_other")
        self.client.force_authenticate(self.me)
        self.url = reverse("post-muted-accounts")
        self.detail = reverse("post-unmute-account", args=[self.other.id])

    def _row(self):
        return MutedAccount.objects.get(user=self.me, muted_user=self.other)

    def test_old_contract_mutes_everything(self):
        r = self.client.post(self.url, {"user_id": self.other.id}, format="json")
        self.assertEqual(r.status_code, 201)
        row = self._row()
        self.assertTrue(row.mute_posts and row.mute_stories)
        self.assertTrue(r.json()["data"]["mute_posts"] and r.json()["data"]["mute_stories"])

    def test_posts_only_leaves_stories_unmuted(self):
        self.client.post(self.url, {"user_id": self.other.id, "posts": True}, format="json")
        row = self._row()
        self.assertTrue(row.mute_posts)
        self.assertFalse(row.mute_stories)

    def test_stories_only(self):
        self.client.post(self.url, {"user_id": self.other.id, "stories": True}, format="json")
        row = self._row()
        self.assertFalse(row.mute_posts)
        self.assertTrue(row.mute_stories)

    def test_both_false_is_rejected(self):
        r = self.client.post(self.url, {"user_id": self.other.id, "posts": False, "stories": False}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(MutedAccount.objects.filter(user=self.me).exists())

    def test_post_again_changes_scope_and_says_so(self):
        self.client.post(self.url, {"user_id": self.other.id}, format="json")
        r = self.client.post(self.url, {"user_id": self.other.id, "posts": True}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["message"], "Mute updated.")
        self.assertFalse(self._row().mute_stories)

    def test_patch_changes_scope_keeping_the_other_flag(self):
        self.client.post(self.url, {"user_id": self.other.id}, format="json")
        r = self.client.patch(self.detail, {"stories": False}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["removed"])
        row = self._row()
        self.assertTrue(row.mute_posts)
        self.assertFalse(row.mute_stories)

    def test_patch_turning_everything_off_unmutes(self):
        self.client.post(self.url, {"user_id": self.other.id, "posts": True}, format="json")
        r = self.client.patch(self.detail, {"posts": False}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["removed"])
        self.assertFalse(MutedAccount.objects.filter(user=self.me).exists())

    def test_patch_unknown_mute_is_404_and_empty_body_is_400(self):
        self.assertEqual(self.client.patch(self.detail, {"posts": True}, format="json").status_code, 404)
        self.client.post(self.url, {"user_id": self.other.id}, format="json")
        self.assertEqual(self.client.patch(self.detail, {}, format="json").status_code, 400)

    def test_list_search(self):
        self.client.post(self.url, {"user_id": self.other.id}, format="json")
        self.assertEqual(len(self.client.get(self.url, {"q": "ms_oth"}).json()["results"]), 1)
        self.assertEqual(len(self.client.get(self.url, {"q": "zzz"}).json()["results"]), 0)


class MuteScopeEffectTests(APITestCase):
    """The flags must actually change what is hidden - not just what the API stores."""

    def setUp(self):
        self.me = _user("mse_me")
        self.author = _user("mse_author")
        self.post = Post.objects.create(
            user=self.author, post_type="text", content="hello", visibility="public", moderation_status="approved"
        )

    def _visible(self):
        return exclude_hidden_and_muted(Post.objects.filter(pk=self.post.pk), self.me).exists()

    def test_posts_mute_hides_posts(self):
        MutedAccount.objects.create(user=self.me, muted_user=self.author, mute_posts=True, mute_stories=False)
        self.assertFalse(self._visible())

    def test_stories_only_mute_keeps_posts_visible(self):
        MutedAccount.objects.create(user=self.me, muted_user=self.author, mute_posts=False, mute_stories=True)
        self.assertTrue(self._visible())

    def test_legacy_default_row_hides_posts(self):
        MutedAccount.objects.create(user=self.me, muted_user=self.author)  # defaults True / True
        self.assertFalse(self._visible())
