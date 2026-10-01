"""
STORIES UPGRADE - PART 1 (Close Friends) tests.

Covers: Story.audience on create/serialize, the visibility rule on every
story read path (list / view / react / reply / viewers), the Close Friends
management API (list, add, remove, replace, candidates) and the block
clean-up signal.
"""
import io
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image
from rest_framework.test import APITestCase

from user_profile.models import BlockUser, Follow

from .models import CloseFriend, Story

User = get_user_model()

_TMP_MEDIA = tempfile.mkdtemp(prefix="ls_story_tests_")


def make_user(username, **extra):
    return User.objects.create_user(username=username, password="testpass123", **extra)


def follow(follower, following):
    return Follow.objects.create(follower=follower, following=following, status=Follow.Status.ACCEPTED)


def make_story(user, audience=Story.AUDIENCE_EVERYONE):
    return Story.objects.create(
        user=user, media="stories/2026/01/01/test.jpg", media_type="image", audience=audience,
    )


def image_upload(name="s.jpg"):
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (200, 30, 30)).save(buf, format="JPEG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class StoryAudienceCreateTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.owner = make_user("cf_owner")
        self.client.force_authenticate(self.owner)

    def _create(self, **data):
        payload = {"media": image_upload(), "media_type": "image", **data}
        return self.client.post(reverse("story-create"), payload, format="multipart")

    def test_audience_defaults_to_everyone(self):
        res = self._create()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["audience"], "everyone")

    def test_can_create_close_friends_story(self):
        res = self._create(audience="close_friends")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["audience"], "close_friends")
        self.assertEqual(Story.objects.get(id=res.data["id"]).audience, "close_friends")

    def test_invalid_audience_is_a_400(self):
        res = self._create(audience="nobody")
        self.assertEqual(res.status_code, 400)
        self.assertIn("audience", res.data["errors"])


class StoryVisibilityTests(APITestCase):
    def setUp(self):
        self.owner = make_user("vis_owner")
        self.friend = make_user("vis_friend")      # follower AND on the list
        self.follower = make_user("vis_follower")  # follower, NOT on the list
        follow(self.friend, self.owner)
        follow(self.follower, self.owner)
        CloseFriend.objects.create(owner=self.owner, friend=self.friend)
        self.public = make_story(self.owner)
        self.private = make_story(self.owner, Story.AUDIENCE_CLOSE_FRIENDS)

    def _list_ids(self, user):
        self.client.force_authenticate(user)
        res = self.client.get(reverse("story-list"))
        self.assertEqual(res.status_code, 200)
        rows = res.data["results"] if isinstance(res.data, dict) else res.data
        return {str(r["id"]) for r in rows}

    def test_owner_sees_both(self):
        self.assertEqual(self._list_ids(self.owner), {str(self.public.id), str(self.private.id)})

    def test_close_friend_sees_both(self):
        self.assertEqual(self._list_ids(self.friend), {str(self.public.id), str(self.private.id)})

    def test_regular_follower_only_sees_public(self):
        self.assertEqual(self._list_ids(self.follower), {str(self.public.id)})

    def test_list_exposes_audience(self):
        self.client.force_authenticate(self.friend)
        res = self.client.get(reverse("story-list"))
        rows = res.data["results"] if isinstance(res.data, dict) else res.data
        by_id = {str(r["id"]): r for r in rows}
        self.assertEqual(by_id[str(self.private.id)]["audience"], "close_friends")
        self.assertEqual(by_id[str(self.public.id)]["audience"], "everyone")

    def test_view_react_reply_blocked_for_non_close_friend(self):
        self.client.force_authenticate(self.follower)
        sid = self.private.id
        self.assertEqual(self.client.post(reverse("story-view", args=[sid])).status_code, 404)
        self.assertEqual(
            self.client.post(reverse("story-react", args=[sid]), {"emoji": "🔥"}, format="json").status_code, 404
        )
        self.assertEqual(
            self.client.post(reverse("story-reply", args=[sid]), {"text": "hi"}, format="json").status_code, 404
        )
        self.assertFalse(self.private.views.exists())

    def test_view_and_react_allowed_for_close_friend(self):
        self.client.force_authenticate(self.friend)
        sid = self.private.id
        self.assertEqual(self.client.post(reverse("story-view", args=[sid])).status_code, 200)
        res = self.client.post(reverse("story-react", args=[sid]), {"emoji": "🔥"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["reacted"])

    def test_reply_allowed_for_close_friend(self):
        self.client.force_authenticate(self.friend)
        res = self.client.post(
            reverse("story-reply", args=[self.private.id]), {"text": "nice"}, format="json"
        )
        self.assertEqual(res.status_code, 201, getattr(res, "data", None))

    def test_public_story_still_open_to_non_close_friend(self):
        self.client.force_authenticate(self.follower)
        self.assertEqual(self.client.post(reverse("story-view", args=[self.public.id])).status_code, 200)

    def test_removed_close_friend_loses_access_immediately(self):
        CloseFriend.objects.filter(owner=self.owner, friend=self.friend).delete()
        self.assertEqual(self._list_ids(self.friend), {str(self.public.id)})
        self.assertEqual(self.client.post(reverse("story-view", args=[self.private.id])).status_code, 404)

    def test_close_friends_is_one_way(self):
        # owner is NOT on friend's list, so friend's close-friends story is hidden from owner.
        follow(self.owner, self.friend)
        hidden = make_story(self.friend, Story.AUDIENCE_CLOSE_FRIENDS)
        self.assertNotIn(str(hidden.id), self._list_ids(self.owner))

    def test_only_owner_can_read_viewers(self):
        self.client.force_authenticate(self.friend)
        res = self.client.get(reverse("story-viewers", args=[self.private.id]))
        self.assertEqual(res.status_code, 403)


class CloseFriendsAPITests(APITestCase):
    def setUp(self):
        self.me = make_user("cfa_me")
        self.a = make_user("cfa_alice", first_name="Alice", last_name="Sharma")
        self.b = make_user("cfa_bob")
        self.c = make_user("cfa_carol")
        self.stranger = make_user("cfa_stranger")
        follow(self.a, self.me)      # alice follows me
        follow(self.me, self.b)      # I follow bob
        follow(self.c, self.me)
        self.client.force_authenticate(self.me)

    def _results(self, res):
        return res.data["results"] if isinstance(res.data, dict) and "results" in res.data else res.data

    def test_requires_auth(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(reverse("close-friends")).status_code, 401)

    def test_add_list_remove(self):
        url = reverse("close-friend-detail", args=[self.a.id])
        self.assertEqual(self.client.post(url).status_code, 201)
        self.assertEqual(self.client.post(url).status_code, 200)  # idempotent
        rows = self._results(self.client.get(reverse("close-friends")))
        self.assertEqual([r["username"] for r in rows], ["cfa_alice"])
        self.assertTrue(rows[0]["is_close_friend"])
        self.assertEqual(rows[0]["name"], "Alice Sharma")
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(self.client.delete(url).status_code, 204)  # idempotent
        self.assertEqual(self._results(self.client.get(reverse("close-friends"))), [])

    def test_cannot_add_self(self):
        res = self.client.post(reverse("close-friend-detail", args=[self.me.id]))
        self.assertEqual(res.status_code, 400)
        self.assertFalse(CloseFriend.objects.exists())

    def test_unknown_user_is_404(self):
        self.assertEqual(self.client.post(reverse("close-friend-detail", args=[999999])).status_code, 404)

    def test_cannot_add_blocked_user_either_direction(self):
        BlockUser.objects.create(blocker=self.me, blocked=self.a)
        BlockUser.objects.create(blocker=self.b, blocked=self.me)
        self.assertEqual(self.client.post(reverse("close-friend-detail", args=[self.a.id])).status_code, 404)
        self.assertEqual(self.client.post(reverse("close-friend-detail", args=[self.b.id])).status_code, 404)

    def test_replace_list(self):
        CloseFriend.objects.create(owner=self.me, friend=self.a)
        res = self.client.put(
            reverse("close-friends"),
            {"user_ids": [self.b.id, self.c.id, self.c.id, self.me.id, 999999]},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["count"], 2)
        self.assertEqual(res.data["skipped_user_ids"], [999999])
        ids = set(CloseFriend.objects.filter(owner=self.me).values_list("friend_id", flat=True))
        self.assertEqual(ids, {self.b.id, self.c.id})

    def test_replace_with_empty_clears_list(self):
        CloseFriend.objects.create(owner=self.me, friend=self.a)
        res = self.client.put(reverse("close-friends"), {"user_ids": []}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 0)

    def test_replace_rejects_bad_body(self):
        self.assertEqual(self.client.put(reverse("close-friends"), {}, format="json").status_code, 400)
        self.assertEqual(
            self.client.put(reverse("close-friends"), {"user_ids": ["x"]}, format="json").status_code, 400
        )

    def test_list_is_private_to_owner(self):
        CloseFriend.objects.create(owner=self.a, friend=self.me)
        self.assertEqual(self._results(self.client.get(reverse("close-friends"))), [])

    def test_candidates_are_followers_and_following_only(self):
        rows = self._results(self.client.get(reverse("close-friends-candidates")))
        self.assertEqual({r["username"] for r in rows}, {"cfa_alice", "cfa_bob", "cfa_carol"})
        self.assertNotIn("cfa_stranger", {r["username"] for r in rows})
        self.assertNotIn("cfa_me", {r["username"] for r in rows})

    def test_candidates_flag_and_search(self):
        CloseFriend.objects.create(owner=self.me, friend=self.a)
        rows = self._results(self.client.get(reverse("close-friends-candidates")))
        flags = {r["username"]: r["is_close_friend"] for r in rows}
        self.assertEqual(flags, {"cfa_alice": True, "cfa_bob": False, "cfa_carol": False})
        found = self._results(self.client.get(reverse("close-friends-candidates"), {"q": "sharma"}))
        self.assertEqual([r["username"] for r in found], ["cfa_alice"])

    def test_candidates_exclude_blocked(self):
        BlockUser.objects.create(blocker=self.me, blocked=self.b)
        rows = self._results(self.client.get(reverse("close-friends-candidates")))
        self.assertNotIn("cfa_bob", {r["username"] for r in rows})

    def test_candidates_query_count_is_constant(self):
        for i in range(15):
            u = make_user(f"cfa_bulk{i}")
            follow(u, self.me)
        with self.assertNumQueries(5):  # 2 block lookups + count + page + close-friend flags
            self.client.get(reverse("close-friends-candidates"), {"page_size": 50})


class BlockCleansCloseFriendsTests(APITestCase):
    def setUp(self):
        self.x = make_user("blk_x")
        self.y = make_user("blk_y")
        CloseFriend.objects.create(owner=self.x, friend=self.y)
        CloseFriend.objects.create(owner=self.y, friend=self.x)

    def test_block_removes_both_directions(self):
        BlockUser.objects.create(blocker=self.x, blocked=self.y)
        self.assertFalse(CloseFriend.objects.exists())

    def test_unrelated_rows_untouched(self):
        z = make_user("blk_z")
        CloseFriend.objects.create(owner=self.x, friend=z)
        BlockUser.objects.create(blocker=self.x, blocked=self.y)
        self.assertEqual(CloseFriend.objects.count(), 1)
