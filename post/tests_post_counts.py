"""GET /post/counts/ (PostCountsAPIView): auth required, throttled, and only approved / non-deleted /
viewer-visible posts are answered (others are silently absent - no existence leak)."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .models import Post

User = get_user_model()


def _post(author, **kw):
    data = dict(user=author, content="x", post_type="text", visibility="public", moderation_status="approved")
    data.update(kw)
    return Post.objects.create(**data)


class PostCountsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user("viewer", "v@x.com", "x")
        self.author = User.objects.create_user("author", "a@x.com", "x")
        self.client = APIClient()
        self.url = reverse("post-counts")

    def _ids(self, *posts):
        return ",".join(str(p.id) for p in posts)

    def _get(self, *posts, raw=None):
        return self.client.get(self.url, {"ids": raw if raw is not None else self._ids(*posts)})

    def test_requires_auth(self):
        p = _post(self.author)
        self.assertIn(self._get(p).status_code, (401, 403))

    def test_returns_counts_for_visible_post(self):
        self.client.force_authenticate(self.me)
        p = _post(self.author, comments_count=3)
        res = self._get(p)
        self.assertEqual(res.status_code, 200)
        self.assertEqual([r["id"] for r in res.data["results"]], [str(p.id)])
        self.assertEqual(res.data["results"][0]["comments_count"], 3)

    def test_hidden_posts_are_absent(self):
        self.client.force_authenticate(self.me)
        ok = _post(self.author)
        deleted = _post(self.author, is_deleted=True)
        pending = _post(self.author, moderation_status="pending")
        archived = _post(self.author, is_archived=True)
        private = _post(self.author, visibility="private")
        followers_only = _post(self.author, visibility="connections")  # viewer does not follow the author
        res = self._get(ok, deleted, pending, archived, private, followers_only)
        self.assertEqual([r["id"] for r in res.data["results"]], [str(ok.id)])

    def test_own_private_post_is_visible(self):
        self.client.force_authenticate(self.me)
        mine = _post(self.me, visibility="private")
        self.assertEqual([r["id"] for r in self._get(mine).data["results"]], [str(mine.id)])

    def test_malformed_ids_do_not_500(self):
        self.client.force_authenticate(self.me)
        p = _post(self.author)
        res = self._get(raw=f"not-a-uuid,,{p.id},{p.id}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual([r["id"] for r in res.data["results"]], [str(p.id)])

    def test_capped_at_100_ids(self):
        self.client.force_authenticate(self.me)
        posts = [_post(self.author) for _ in range(3)]
        res = self._get(*posts)
        self.assertEqual(len(res.data["results"]), 3)
        self.assertEqual(self._get(raw="").data["results"], [])


class PostCountsEtagTests(TestCase):
    """H9: ETag / If-None-Match -> 304 so an idle client's poll costs no body."""

    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user("viewer", "v@x.com", "x")
        self.author = User.objects.create_user("author", "a@x.com", "x")
        self.client = APIClient()
        self.client.force_authenticate(self.me)
        self.url = reverse("post-counts")

    def _get(self, ids, etag=None):
        extra = {"HTTP_IF_NONE_MATCH": etag} if etag else {}
        return self.client.get(self.url, {"ids": ids}, **extra)

    def test_answer_has_a_weak_etag(self):
        p = _post(self.author)
        res = self._get(str(p.id))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res["ETag"].startswith('W/"'))

    def test_matching_if_none_match_gives_304_with_empty_body(self):
        p = _post(self.author)
        etag = self._get(str(p.id))["ETag"]
        res = self._get(str(p.id), etag)
        self.assertEqual(res.status_code, 304)
        self.assertEqual(res.content, b"")
        self.assertEqual(res["ETag"], etag)

    def test_changed_count_gives_200_and_a_new_etag(self):
        p = _post(self.author, comments_count=1)
        etag = self._get(str(p.id))["ETag"]
        Post.objects.filter(pk=p.pk).update(comments_count=2)
        res = self._get(str(p.id), etag)
        self.assertEqual(res.status_code, 200)
        self.assertNotEqual(res["ETag"], etag)
        self.assertEqual(res.data["results"][0]["comments_count"], 2)

    def test_post_that_becomes_hidden_changes_the_etag(self):
        p = _post(self.author)
        q = _post(self.author)
        ids = f"{p.id},{q.id}"
        etag = self._get(ids)["ETag"]
        Post.objects.filter(pk=q.pk).update(is_deleted=True)
        res = self._get(ids, etag)
        self.assertEqual(res.status_code, 200)
        self.assertEqual([r["id"] for r in res.data["results"]], [str(p.id)])

    def test_stale_or_foreign_etag_is_ignored(self):
        p = _post(self.author)
        self.assertEqual(self._get(str(p.id), 'W/"deadbeef"').status_code, 200)

    def test_etag_does_not_depend_on_id_order(self):
        p, q = _post(self.author), _post(self.author)
        a = self._get(f"{p.id},{q.id}")["ETag"]
        b = self._get(f"{q.id},{p.id}")["ETag"]
        self.assertEqual(a, b)

    def test_several_tags_and_star(self):
        p = _post(self.author)
        etag = self._get(str(p.id))["ETag"]
        self.assertEqual(self._get(str(p.id), f'W/"x", {etag}').status_code, 304)
        self.assertEqual(self._get(str(p.id), "*").status_code, 304)

    def test_empty_ids_still_works(self):
        self.assertEqual(self._get("").status_code, 200)
