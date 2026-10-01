"""
post/test_pinned_posts.py

P3-BE — pinned posts: POST/DELETE /post/<id>/pin/ (max 3, owner only) and
pinned-first ordering on GET /post/list/ (profile grid).

Run:  python manage.py test post.test_pinned_posts

Assumes the project's user model accepts
`create_user(username=, email=, password=)` and that `Post.objects.create(...)`
works with the handful of fields used in `_post()` below — if your models
need more required fields, only `_user()` / `_post()` need adjusting.
"""
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Post

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


class PinEndpointTests(APITestCase):
    def setUp(self):
        self.me = _user("pin_me")
        self.other = _user("pin_other")
        self.client.force_authenticate(self.me)
        self.posts = [_post(self.me) for _ in range(5)]

    def _pin(self, post):
        return self.client.post(reverse("post-pin", kwargs={"post_id": post.id}))

    def _unpin(self, post):
        return self.client.delete(reverse("post-pin", kwargs={"post_id": post.id}))

    def _pinned_ids(self):
        return set(Post.objects.filter(user=self.me, is_pinned=True).values_list("id", flat=True))

    def test_pin_sets_flag_and_reports_count(self):
        r = self._pin(self.posts[0])
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertTrue(r.json()["data"]["is_pinned"])
        self.assertEqual(r.json()["data"]["pinned_count"], 1)
        self.assertEqual(r.json()["data"]["max_pinned"], 3)
        self.assertEqual(self._pinned_ids(), {self.posts[0].id})

    def test_pin_is_idempotent(self):
        self._pin(self.posts[0])
        r = self._pin(self.posts[0])
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.json()["data"]["pinned_count"], 1)

    def test_fourth_pin_is_rejected_with_clear_error(self):
        for p in self.posts[:3]:
            self.assertEqual(self._pin(p).status_code, 200)
        r = self._pin(self.posts[3])
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        body = r.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["code"], "pin_limit_reached")
        self.assertEqual(body["max_pinned"], 3)
        self.assertIn("3", body["message"])
        self.assertEqual(self._pinned_ids(), {p.id for p in self.posts[:3]})

    def test_repinning_an_already_pinned_post_at_the_limit_still_succeeds(self):
        for p in self.posts[:3]:
            self._pin(p)
        self.assertEqual(self._pin(self.posts[0]).status_code, 200)

    def test_unpin_works_and_frees_a_slot(self):
        for p in self.posts[:3]:
            self._pin(p)
        r = self._unpin(self.posts[0])
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertFalse(r.json()["data"]["is_pinned"])
        self.assertEqual(r.json()["data"]["pinned_count"], 2)
        self.assertEqual(self._pin(self.posts[3]).status_code, 200)
        self.assertEqual(self._pinned_ids(), {self.posts[1].id, self.posts[2].id, self.posts[3].id})

    def test_unpin_is_idempotent(self):
        r = self._unpin(self.posts[0])
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.json()["data"]["pinned_count"], 0)

    def test_only_the_owner_can_pin_or_unpin(self):
        theirs = _post(self.other)
        self.assertEqual(self._pin(theirs).status_code, status.HTTP_403_FORBIDDEN)
        Post.objects.filter(pk=theirs.pk).update(is_pinned=True)
        self.assertEqual(self._unpin(theirs).status_code, status.HTTP_403_FORBIDDEN)
        theirs.refresh_from_db()
        self.assertTrue(theirs.is_pinned)  # untouched

    def test_unknown_or_deleted_post_is_404(self):
        self.posts[0].is_deleted = True
        self.posts[0].deleted_at = timezone.now()
        self.posts[0].save(update_fields=["is_deleted", "deleted_at"])
        self.assertEqual(self._pin(self.posts[0]).status_code, status.HTTP_404_NOT_FOUND)

    def test_soft_deleted_pinned_post_does_not_use_up_a_slot(self):
        for p in self.posts[:3]:
            self._pin(p)
        gone = self.posts[0]
        gone.is_deleted = True
        gone.deleted_at = timezone.now()
        gone.save(update_fields=["is_deleted", "deleted_at"])
        self.assertEqual(self._pin(self.posts[3]).status_code, 200)

    def test_post_that_is_not_approved_cannot_be_pinned(self):
        p = _post(self.me, moderation_status="pending")
        r = self._pin(p)
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(r.json()["code"], "post_not_pinnable")

    def test_limits_are_per_user(self):
        for p in self.posts[:3]:
            self._pin(p)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(reverse("post-pin", kwargs={"post_id": _post(self.other).id})).status_code, 200)

    def test_requires_authentication(self):
        self.client.force_authenticate(None)
        r = self._pin(self.posts[0])
        self.assertIn(r.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))


class PinnedOrderingTests(APITestCase):
    def setUp(self):
        self.me = _user("ord_me")
        self.other = _user("ord_other")
        now = timezone.now()
        # Oldest first in time: p0 (oldest) ... p4 (newest)
        self.mine = [_post(self.me) for _ in range(5)]
        self.theirs = [_post(self.other) for _ in range(5)]
        for group in (self.mine, self.theirs):
            for i, p in enumerate(group):
                Post.objects.filter(pk=p.pk).update(created_at=now - timedelta(hours=10 - i))

    def _ids(self, **params):
        r = self.client.get(reverse("post-list"), params)
        self.assertEqual(r.status_code, 200)
        return [row["id"] for row in _results(r)]

    def test_own_profile_lists_pinned_first_then_newest(self):
        self.client.force_authenticate(self.me)
        Post.objects.filter(pk__in=[self.mine[0].pk, self.mine[2].pk]).update(is_pinned=True)
        ids = self._ids()
        self.assertEqual(
            ids,
            [str(self.mine[2].pk), str(self.mine[0].pk), str(self.mine[4].pk), str(self.mine[3].pk), str(self.mine[1].pk)],
        )

    def test_other_users_profile_lists_pinned_first(self):
        self.client.force_authenticate(self.me)
        Post.objects.filter(pk=self.theirs[0].pk).update(is_pinned=True)  # the OLDEST one
        ids = self._ids(target_user_id=str(self.other.id))
        self.assertEqual(ids[0], str(self.theirs[0].pk))
        self.assertEqual(ids[1:], [str(p.pk) for p in reversed(self.theirs[1:])])

    def test_no_pins_keeps_newest_first(self):
        self.client.force_authenticate(self.me)
        self.assertEqual(self._ids(), [str(p.pk) for p in reversed(self.mine)])

    def test_explicit_ordering_param_overrides_pinned_first(self):
        self.client.force_authenticate(self.me)
        Post.objects.filter(pk=self.mine[0].pk).update(is_pinned=True)
        ids = self._ids(ordering="created_at")
        self.assertEqual(ids[0], str(self.mine[0].pk))  # oldest first, and it happens to be the pinned one
        self.assertEqual(ids, [str(p.pk) for p in self.mine])

    def test_list_serializer_exposes_is_pinned(self):
        self.client.force_authenticate(self.me)
        Post.objects.filter(pk=self.mine[1].pk).update(is_pinned=True)
        r = self.client.get(reverse("post-list"))
        by_id = {row["id"]: row for row in _results(r)}
        self.assertTrue(by_id[str(self.mine[1].pk)]["is_pinned"])
        self.assertFalse(by_id[str(self.mine[2].pk)]["is_pinned"])
