"""P2 - profile tab filters/sort + saved collections. NOT RUN.   python manage.py test post.tests_profile_tabs"""
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Post, PostSave, SavedCollection

User = get_user_model()


def _user(name):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345")


def _post(user, **kw):
    f = dict(user=user, post_type="text", content="hi", visibility="public", moderation_status="approved")
    f.update(kw)
    return Post.objects.create(**f)


def _rows(resp):
    d = resp.json()
    return d["results"] if isinstance(d, dict) and "results" in d else d


class ProfileFilterSortTests(APITestCase):
    def setUp(self):
        self.me = _user("p2_me")
        self.client.force_authenticate(self.me)

    def test_kind_and_post_type_list_filters_work_on_my_own_grid(self):
        _post(self.me, kind="resource", post_type="document")
        _post(self.me, kind="post", post_type="image")
        r = self.client.get("/post/list/", {"kind": "resource"})
        self.assertEqual(len(_rows(r)), 1)
        r = self.client.get("/post/list/", {"post_type": "document,pdf"})
        self.assertEqual(len(_rows(r)), 1)
        self.assertEqual(len(_rows(self.client.get("/post/list/", {"kind": "nonsense"}))), 0)

    def test_popular_sort_keeps_pinned_first(self):
        low = _post(self.me, likes_count=1, is_pinned=True)
        top = _post(self.me, likes_count=50)
        mid = _post(self.me, likes_count=10)
        ids = [x["id"] for x in _rows(self.client.get("/post/list/", {"sort": "popular"}))]
        self.assertEqual(ids, [str(low.id), str(top.id), str(mid.id)])


class SavedCollectionTests(APITestCase):
    def setUp(self):
        self.me = _user("p2_owner")
        self.other = _user("p2_other")
        self.client.force_authenticate(self.me)
        self.post = _post(self.other)

    def _create(self, name="JEE", **kw):
        return self.client.post("/post/saved/collections/", {"name": name, **kw}, format="json")

    def test_create_is_private_by_default_and_names_are_validated(self):
        r = self._create()
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.json()["data"]["is_private"])
        self.assertEqual(self._create("jee").status_code, 409)  # case-insensitive duplicate
        self.assertEqual(self._create("default").status_code, 400)
        self.assertEqual(self._create("  ").status_code, 400)

    def test_save_into_folder_move_rename_delete(self):
        self._create("Maths")
        r = self.client.post(f"/post/{self.post.id}/save/", {"collection_name": "Maths"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(len(_rows(self.client.get("/post/saved/", {"collection": "Maths"}))), 1)
        cid = SavedCollection.objects.get(user=self.me, name="Maths").id
        self.client.patch(f"/post/saved/collections/{cid}/", {"name": "Maths 2"}, format="json")
        self.assertEqual(PostSave.objects.get(user=self.me).collection_name, "Maths 2")
        self.client.put(f"/post/saved/{self.post.id}/collection/", {"collection_name": "default"}, format="json")
        self.assertEqual(PostSave.objects.get(user=self.me).collection_name, "default")
        self.client.delete(f"/post/saved/collections/{cid}/")
        self.assertEqual(PostSave.objects.filter(user=self.me).count(), 1)  # posts are never deleted with a folder

    def test_unknown_folder_is_refused(self):
        r = self.client.post(f"/post/{self.post.id}/save/", {"collection_name": "Nope"}, format="json")
        self.assertEqual(r.status_code, 404)

    def test_collection_filter_ignores_other_peoples_saves(self):
        self._create("Mine")
        PostSave.objects.create(post=self.post, user=self.other, collection_name="Mine")  # someone else's row
        self.assertEqual(len(_rows(self.client.get("/post/saved/", {"collection": "Mine"}))), 0)

    def test_private_folder_is_invisible_to_others_public_one_is_listed(self):
        self._create("Secret")
        self._create("Open", is_private=False)
        PostSave.objects.create(post=self.post, user=self.me, collection_name="Open")
        PostSave.objects.create(post=_post(self.other), user=self.me, collection_name="Secret")
        self.client.force_authenticate(self.other)
        names = [c["name"] for c in self.client.get(f"/post/collections/of/{self.me.username}/").json()["data"]["collections"]]
        self.assertEqual(names, ["Open"])
        self.assertEqual(self.client.get(f"/post/collections/of/{self.me.username}/Secret/posts/").status_code, 404)
        self.assertEqual(self.client.get(f"/post/collections/of/{self.me.username}/Open/posts/").status_code, 200)
