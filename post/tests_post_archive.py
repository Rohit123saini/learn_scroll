"""
post/tests_post_archive.py

Feed card menu "Archive": POST/DELETE /post/<id>/archive/ and GET /post/archived/.
Written, NOT run.   Run:  python manage.py test post.tests_post_archive
"""
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Hashtag, Post

User = get_user_model()


def _user(name):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345")


def _post(user, **kw):
    fields = dict(user=user, post_type="text", content="hello", visibility="public", moderation_status="approved")
    fields.update(kw)
    return Post.objects.create(**fields)


def _results(resp):
    data = resp.json()
    return data["results"] if isinstance(data, dict) and "results" in data else data


class ArchiveEndpointTests(APITestCase):
    def setUp(self):
        self.me = _user("arc_me")
        self.other = _user("arc_other")
        self.client.force_authenticate(self.me)
        self.post_obj = _post(self.me)

    def _archive(self, post):
        return self.client.post(reverse("post-archive", kwargs={"post_id": post.id}))

    def _restore(self, post):
        return self.client.delete(reverse("post-archive", kwargs={"post_id": post.id}))

    def test_archive_sets_flags_and_is_idempotent(self):
        r = self._archive(self.post_obj)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertTrue(r.json()["data"]["is_archived"])
        self.post_obj.refresh_from_db()
        self.assertTrue(self.post_obj.is_archived)
        self.assertIsNotNone(self.post_obj.archived_at)
        self.assertEqual(self._archive(self.post_obj).status_code, status.HTTP_200_OK)

    def test_restore_clears_flags_and_is_idempotent(self):
        self._archive(self.post_obj)
        r = self._restore(self.post_obj)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertFalse(r.json()["data"]["is_archived"])
        self.post_obj.refresh_from_db()
        self.assertFalse(self.post_obj.is_archived)
        self.assertIsNone(self.post_obj.archived_at)
        self.assertEqual(self._restore(self.post_obj).status_code, status.HTTP_200_OK)

    def test_only_the_owner_can_archive_or_restore(self):
        theirs = _post(self.other)
        self.assertEqual(self._archive(theirs).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self._restore(theirs).status_code, status.HTTP_403_FORBIDDEN)
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_archived)

    def test_deleted_post_is_404(self):
        self.post_obj.is_deleted = True
        self.post_obj.save(update_fields=["is_deleted"])
        self.assertEqual(self._archive(self.post_obj).status_code, status.HTTP_404_NOT_FOUND)

    def test_scheduled_post_cannot_be_archived(self):
        sched = _post(self.me, is_scheduled=True)
        r = self._archive(sched)
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(r.json()["code"], "post_not_archivable")

    def test_archiving_unpins_and_pinning_an_archived_post_is_refused(self):
        self.client.post(reverse("post-pin", kwargs={"post_id": self.post_obj.id}))
        self.post_obj.refresh_from_db()
        self.assertTrue(self.post_obj.is_pinned)
        self._archive(self.post_obj)
        self.post_obj.refresh_from_db()
        self.assertFalse(self.post_obj.is_pinned)
        r = self.client.post(reverse("post-pin", kwargs={"post_id": self.post_obj.id}))
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_posts_count_follows_archive_and_restore(self):
        User.objects.filter(pk=self.me.pk).update(posts_count=3)
        self._archive(self.post_obj)
        self._archive(self.post_obj)  # idempotent: no second decrement
        self.me.refresh_from_db()
        self.assertEqual(self.me.posts_count, 2)
        self._restore(self.post_obj)
        self._restore(self.post_obj)
        self.me.refresh_from_db()
        self.assertEqual(self.me.posts_count, 3)

    def test_hashtag_counts_follow_archive_and_restore(self):
        p = _post(self.me, hashtags=["physics"])
        Hashtag.objects.update_or_create(name="physics", defaults={"posts_count": 1})
        self._archive(p)
        self.assertEqual(Hashtag.objects.get(name="physics").posts_count, 0)
        self._restore(p)
        self.assertEqual(Hashtag.objects.get(name="physics").posts_count, 1)

    def test_archived_list_is_mine_only_and_restore_removes_it(self):
        theirs = _post(self.other)
        self.client.force_authenticate(self.other)
        self._archive(theirs)
        self.client.force_authenticate(self.me)
        self._archive(self.post_obj)
        ids = {row["id"] for row in _results(self.client.get(reverse("post-archived-list")))}
        self.assertEqual(ids, {str(self.post_obj.id)})
        self._restore(self.post_obj)
        self.assertEqual(_results(self.client.get(reverse("post-archived-list"))), [])


class ArchivedPostIsHiddenTests(APITestCase):
    def setUp(self):
        self.me = _user("arh_me")
        self.viewer = _user("arh_viewer")
        self.post_obj = _post(self.me, hashtags=["chem"])
        self.client.force_authenticate(self.me)
        self.client.post(reverse("post-archive", kwargs={"post_id": self.post_obj.id}))

    def test_direct_link_is_404_for_others_but_owner_still_opens_it(self):
        self.client.force_authenticate(self.viewer)
        r = self.client.get(reverse("post-detail", kwargs={"id": self.post_obj.id}))
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)
        self.client.force_authenticate(self.me)
        r = self.client.get(reverse("post-detail", kwargs={"id": self.post_obj.id}))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertTrue(r.json()["data"]["is_archived"])

    def test_profile_grid_hides_it_for_others_and_for_me(self):
        for who, params in ((self.viewer, {"target_user_id": str(self.me.id)}), (self.me, {})):
            self.client.force_authenticate(who)
            rows = _results(self.client.get(reverse("post-list"), params))
            self.assertNotIn(str(self.post_obj.id), {row["id"] for row in rows})

    def test_hashtag_page_hides_it(self):
        self.client.force_authenticate(self.viewer)
        rows = _results(self.client.get(reverse("hashtag-posts", kwargs={"tag": "chem"})))
        self.assertNotIn(str(self.post_obj.id), {row["id"] for row in rows})

    def test_restore_makes_it_visible_again(self):
        self.client.delete(reverse("post-archive", kwargs={"post_id": self.post_obj.id}))
        self.client.force_authenticate(self.viewer)
        r = self.client.get(reverse("post-detail", kwargs={"id": self.post_obj.id}))
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_repost_of_an_archived_post_is_refused(self):
        self.client.force_authenticate(self.viewer)
        r = self.client.post(reverse("post-repost", kwargs={"post_id": self.post_obj.id}), {}, format="json")
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)
