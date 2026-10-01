"""
post/test_post_tags.py

P5a-BE — tagged posts: `tags` on create/edit, POST_TAG notification,
GET /post/tagged/<username>/, PATCH/DELETE /post/<id>/tag/.

Run:  python manage.py test post.test_post_tags

Same assumptions as test_pinned_posts.py (create_user signature, Post fields).
"""
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Notification
from user_profile.models import Follow
from .models import Post, PostTag

User = get_user_model()


def _user(name, **kw):
    u = User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345")
    if kw:
        User.objects.filter(pk=u.pk).update(**kw)
        u.refresh_from_db()
    return u


def _post(user, **kw):
    fields = dict(user=user, post_type="text", content="hello", visibility="public", moderation_status="approved")
    fields.update(kw)
    return Post.objects.create(**fields)


def _results(resp):
    data = resp.json()
    return data["results"] if isinstance(data, dict) and "results" in data else data


def _follow(a, b):
    Follow.objects.create(follower=a, following=b, status=Follow.Status.ACCEPTED)


class TagCreateEditTests(APITestCase):
    def setUp(self):
        self.me = _user("tag_me")
        self.a = _user("tag_a")
        self.b = _user("tag_b")
        self.client.force_authenticate(self.me)

    def _create(self, tags, **extra):
        body = {"post_type": "text", "content": "hi", "tags": tags, **extra}
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("post-create"), body, format="json")

    def _edit(self, post, tags):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.patch(reverse("post-edit", kwargs={"id": post.id}), {"tags": tags}, format="json")

    def test_create_with_tags_persists_and_notifies(self):
        r = self._create([{"user_id": self.a.pk, "x": 0.25, "y": 0.75}, {"user_id": self.b.pk}])
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.content)
        post = Post.objects.get(pk=r.json()["data"]["id"])
        self.assertEqual(set(post.post_tags.values_list("tagged_user_id", flat=True)), {self.a.pk, self.b.pk})
        tag_a = post.post_tags.get(tagged_user=self.a)
        self.assertEqual((tag_a.x, tag_a.y), (0.25, 0.75))
        n = Notification.objects.get(recipient=self.a, notif_type=Notification.NotifType.POST_TAG)
        self.assertEqual(n.data["post_id"], str(post.id))
        self.assertEqual(len(r.json()["data"]["tagged_users"]), 2)

    def test_tagging_yourself_does_not_notify(self):
        self._create([{"user_id": self.me.pk}])
        self.assertFalse(Notification.objects.filter(notif_type=Notification.NotifType.POST_TAG).exists())

    def test_bad_tags_are_rejected(self):
        for bad in (
            [{"user_id": self.a.pk, "x": 0.5}],             # x without y
            [{"user_id": self.a.pk, "x": 1.5, "y": 0.5}],   # out of range
            [{"user_id": "nope"}],                           # bad id
            [{"x": 0.1, "y": 0.1}],                          # missing user_id
        ):
            r = self._create(bad)
            self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST, bad)

    def test_more_than_20_tags_rejected(self):
        users = [_user(f"bulk{i}") for i in range(21)]
        r = self._create([{"user_id": u.pk} for u in users])
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_tag_in_private_post(self):
        r = self._create([{"user_id": self.a.pk}], visibility="private")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_edit_replaces_tags_and_only_notifies_new_people(self):
        post = _post(self.me)
        self._edit(post, [{"user_id": self.a.pk}])
        self.assertEqual(Notification.objects.filter(recipient=self.a, notif_type=Notification.NotifType.POST_TAG).count(), 1)

        # keep a (move it), drop nobody yet, add b
        self._edit(post, [{"user_id": self.a.pk, "x": 0.1, "y": 0.2}, {"user_id": self.b.pk}])
        self.assertEqual(Notification.objects.filter(recipient=self.a, notif_type=Notification.NotifType.POST_TAG).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.b, notif_type=Notification.NotifType.POST_TAG).count(), 1)
        self.assertEqual(post.post_tags.get(tagged_user=self.a).x, 0.1)

        # drop a, keep b; [] clears everything
        self._edit(post, [{"user_id": self.b.pk}])
        self.assertEqual(list(post.post_tags.values_list("tagged_user_id", flat=True)), [self.b.pk])
        self._edit(post, [])
        self.assertEqual(post.post_tags.count(), 0)

    def test_tag_only_edit_does_not_mark_post_edited(self):
        post = _post(self.me)
        self._edit(post, [{"user_id": self.a.pk}])
        post.refresh_from_db()
        self.assertFalse(post.is_edited)

    def test_edit_keeps_is_hidden(self):
        post = _post(self.me)
        self._edit(post, [{"user_id": self.a.pk}])
        PostTag.objects.filter(post=post).update(is_hidden=True)
        self._edit(post, [{"user_id": self.a.pk, "x": 0.3, "y": 0.3}])
        self.assertTrue(post.post_tags.get().is_hidden)

    def test_scheduled_post_is_not_notified_until_published(self):
        from .serializers import notify_post_tags
        post = _post(self.me, is_scheduled=True)
        PostTag.objects.create(post=post, tagged_user=self.a)
        notify_post_tags(post)
        self.assertFalse(Notification.objects.filter(recipient=self.a).exists())
        Post.objects.filter(pk=post.pk).update(is_scheduled=False)
        post.refresh_from_db()
        notify_post_tags(post)
        notify_post_tags(post)  # idempotent
        self.assertEqual(Notification.objects.filter(recipient=self.a, notif_type=Notification.NotifType.POST_TAG).count(), 1)


class TaggedTabTests(APITestCase):
    def setUp(self):
        self.owner = _user("tt_owner")          # the person who is tagged
        self.author = _user("tt_author")
        self.viewer = _user("tt_viewer")
        self.post = _post(self.author)
        self.tag = PostTag.objects.create(post=self.post, tagged_user=self.owner, x=0.5, y=0.5)

    def _get(self, viewer, username=None, **params):
        self.client.force_authenticate(viewer)
        return self.client.get(reverse("post-tagged", kwargs={"username": username or self.owner.username}), params)

    def _ids(self, resp):
        return [row["id"] for row in _results(resp)]

    def test_public_account_tab_lists_tagged_posts_with_tag_info(self):
        r = self._get(self.viewer)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._ids(r), [str(self.post.pk)])
        self.assertEqual(_results(r)[0]["tag"]["x"], 0.5)
        self.assertNotIn("is_hidden", _results(r)[0]["tag"])   # only the owner sees that flag

    def test_unknown_user_404(self):
        self.assertEqual(self._get(self.viewer, username="ghost").status_code, 404)

    def test_username_lookup_is_case_insensitive(self):
        self.assertEqual(self._ids(self._get(self.viewer, username=self.owner.username.upper())), [str(self.post.pk)])

    def test_private_account_tags_only_for_followers(self):
        User.objects.filter(pk=self.owner.pk).update(is_private=True)
        self.assertEqual(self._ids(self._get(self.viewer)), [])
        _follow(self.viewer, self.owner)
        self.assertEqual(self._ids(self._get(self.viewer)), [str(self.post.pk)])
        self.assertEqual(self._ids(self._get(self.owner)), [str(self.post.pk)])   # owner always sees own tab

    def test_post_from_private_author_needs_following_the_author(self):
        User.objects.filter(pk=self.author.pk).update(is_private=True)
        self.assertEqual(self._ids(self._get(self.viewer)), [])
        _follow(self.viewer, self.author)
        self.assertEqual(self._ids(self._get(self.viewer)), [str(self.post.pk)])

    def test_connections_post_needs_following_the_author(self):
        Post.objects.filter(pk=self.post.pk).update(visibility="connections")
        self.assertEqual(self._ids(self._get(self.viewer)), [])
        _follow(self.viewer, self.author)
        self.assertEqual(self._ids(self._get(self.viewer)), [str(self.post.pk)])

    def test_private_post_hidden_from_everyone_but_author(self):
        Post.objects.filter(pk=self.post.pk).update(visibility="private")
        self.assertEqual(self._ids(self._get(self.viewer)), [])
        self.assertEqual(self._ids(self._get(self.owner)), [])
        self.assertEqual(self._ids(self._get(self.author)), [str(self.post.pk)])

    def test_deleted_unapproved_and_scheduled_posts_are_excluded(self):
        for field, value in (("is_deleted", True), ("moderation_status", "pending"), ("is_scheduled", True)):
            Post.objects.filter(pk=self.post.pk).update(**{field: value})
            self.assertEqual(self._ids(self._get(self.viewer)), [], field)
            Post.objects.filter(pk=self.post.pk).update(is_deleted=False, moderation_status="approved", is_scheduled=False)

    def test_hidden_tags_are_off_the_tab_but_owner_can_list_them(self):
        PostTag.objects.filter(pk=self.tag.pk).update(is_hidden=True)
        self.assertEqual(self._ids(self._get(self.viewer)), [])
        self.assertEqual(self._ids(self._get(self.owner)), [])
        r = self._get(self.owner, include_hidden="1")
        self.assertEqual(self._ids(r), [str(self.post.pk)])
        self.assertTrue(_results(r)[0]["tag"]["is_hidden"])
        # include_hidden is ignored for anyone else
        self.assertEqual(self._ids(self._get(self.viewer, include_hidden="1")), [])

    def test_newest_tag_first_and_paginated_shape(self):
        second = _post(self.author)
        PostTag.objects.create(post=second, tagged_user=self.owner)
        r = self._get(self.viewer)
        self.assertEqual(self._ids(r), [str(second.pk), str(self.post.pk)])
        self.assertIn("next", r.json())
        self.assertEqual(r.json()["count"], 2)


class TagManageTests(APITestCase):
    def setUp(self):
        self.author = _user("tm_author")
        self.tagged = _user("tm_tagged")
        self.other = _user("tm_other")
        self.post = _post(self.author)
        PostTag.objects.create(post=self.post, tagged_user=self.tagged)
        self.url = reverse("post-tag", kwargs={"post_id": self.post.id})

    def test_tagged_user_can_hide_and_unhide(self):
        self.client.force_authenticate(self.tagged)
        r = self.client.patch(self.url, {"hidden": True}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(PostTag.objects.get(post=self.post).is_hidden)
        self.client.patch(self.url, {"hidden": False}, format="json")
        self.assertFalse(PostTag.objects.get(post=self.post).is_hidden)

    def test_hide_requires_being_tagged(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.patch(self.url, {"hidden": True}, format="json").status_code, 404)
        self.client.force_authenticate(self.author)
        self.assertEqual(self.client.patch(self.url, {"hidden": True}, format="json").status_code, 404)

    def test_hide_needs_a_boolean(self):
        self.client.force_authenticate(self.tagged)
        self.assertEqual(self.client.patch(self.url, {}, format="json").status_code, 400)

    def test_tagged_user_can_remove_own_tag_idempotently(self):
        self.client.force_authenticate(self.tagged)
        r = self.client.delete(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["data"]["removed"])
        self.assertFalse(PostTag.objects.filter(post=self.post).exists())
        r = self.client.delete(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["data"]["removed"])

    def test_author_can_remove_someone_elses_tag(self):
        self.client.force_authenticate(self.author)
        r = self.client.delete(f"{self.url}?user_id={self.tagged.pk}")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(PostTag.objects.filter(post=self.post).exists())

    def test_stranger_cannot_remove_someone_elses_tag(self):
        self.client.force_authenticate(self.other)
        r = self.client.delete(f"{self.url}?user_id={self.tagged.pk}")
        self.assertEqual(r.status_code, 403)
        self.assertTrue(PostTag.objects.filter(post=self.post).exists())

    def test_requires_authentication(self):
        r = self.client.delete(self.url)
        self.assertIn(r.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
