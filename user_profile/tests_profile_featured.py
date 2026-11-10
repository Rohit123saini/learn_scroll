"""P6 - the single "Featured" slot of a profile (user_profile/featured.py). Written, NOT run."""
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Follow, ProfileFeatured

User = get_user_model()


def _show(username):
    return f"/profile/profile/{username}/featured/"


MINE = "/profile/featured/"
CANDIDATES = "/profile/featured/candidates/"


class FeaturedTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="feat_owner", password="x")
        self.other = User.objects.create_user(username="feat_other", password="x")
        self.c = APIClient()
        self.c.force_authenticate(self.owner)

    # ---- makers ----
    def _test(self, **kw):
        from testseries.models import TestSeries

        data = dict(
            creator=self.owner, title="JEE Mock 1", source=TestSeries.Source.INDIVIDUAL,
            status=TestSeries.Status.PUBLISHED, subject="Physics", duration_minutes=60,
        )
        data.update(kw)
        return TestSeries.objects.create(**data)

    def _class(self, **kw):
        from tuitionclass.models import Classroom

        return Classroom.objects.create(teacher=self.owner, title=kw.pop("title", "Physics 12"), subject="Physics", **kw)

    def _resource(self, **kw):
        from post.models import Post

        data = dict(
            user=self.owner, kind=Post.KIND_RESOURCE, title="Optics notes", content="x", post_type="document",
            visibility="public", moderation_status="approved", subject="Physics", level="Class 12",
        )
        data.update(kw)
        return Post.objects.create(**data)

    def _put(self, kind, target_id):
        return self.c.put(MINE, {"kind": kind, "target_id": str(target_id)}, format="json")

    # ---- set / read ----
    def test_feature_each_kind_and_read_it_back(self):
        for kind, obj in (("test", self._test()), ("class", self._class()), ("resource", self._resource())):
            with self.subTest(kind=kind):
                r = self._put(kind, obj.pk)
                self.assertEqual(r.status_code, 200, r.data)
                card = r.data["featured"]
                self.assertEqual(card["kind"], kind)
                self.assertEqual(card["id"], str(obj.pk))
                self.assertTrue(card["title"])
                again = self.c.get(_show("feat_owner")).data
                self.assertEqual(again["featured"]["id"], str(obj.pk))
                self.assertFalse(again["locked"])

    def test_only_one_slot_the_new_pick_replaces_the_old(self):
        t, c = self._test(), self._class()
        self._put("test", t.pk)
        self._put("class", c.pk)
        self.assertEqual(ProfileFeatured.objects.filter(user=self.owner).count(), 1)
        self.assertEqual(self.c.get(_show("feat_owner")).data["featured"]["kind"], "class")

    def test_clear_is_idempotent(self):
        self._put("test", self._test().pk)
        self.assertEqual(self.c.delete(MINE).status_code, 204)
        self.assertEqual(self.c.delete(MINE).status_code, 204)
        self.assertIsNone(self.c.get(_show("feat_owner")).data["featured"])

    def test_empty_profile_has_null(self):
        d = self.c.get(_show("feat_owner")).data
        self.assertIsNone(d["featured"])
        self.assertFalse(d["locked"])

    # ---- rules: what may be featured ----
    def test_someone_elses_unknown_and_malformed_ids_are_one_error(self):
        from testseries.models import TestSeries

        theirs = TestSeries.objects.create(
            creator=self.other, title="Not mine", source=TestSeries.Source.INDIVIDUAL, status=TestSeries.Status.PUBLISHED,
        )
        for kind, target in (("test", theirs.pk), ("test", uuid.uuid4()), ("test", "not-a-uuid"), ("class", "abc"),
                             ("class", 99999999), ("resource", uuid.uuid4())):
            with self.subTest(kind=kind, target=str(target)):
                r = self._put(kind, target)
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.data["code"], "featured_not_found")
        self.assertFalse(ProfileFeatured.objects.exists())

    def test_unknown_kind_is_400(self):
        r = self._put("video", uuid.uuid4())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["code"], "featured_kind")

    def test_not_public_things_cannot_be_featured(self):
        from testseries.models import TestSeries

        cases = [
            ("test", self._test(status=TestSeries.Status.DRAFT)),
            ("test", self._test(status=TestSeries.Status.ARCHIVED)),
            ("test", self._test(source=TestSeries.Source.CAMPUS)),
            ("class", self._class(is_flagged=True)),
            ("class", self._class(is_active=False)),
            ("resource", self._resource(visibility="private")),
            ("resource", self._resource(visibility="connections")),
            ("resource", self._resource(is_archived=True)),
            ("resource", self._resource(is_deleted=True)),
            ("resource", self._resource(is_scheduled=True)),
            ("resource", self._resource(moderation_status="copyright_hold")),
        ]
        for kind, obj in cases:
            with self.subTest(kind=kind, obj=str(obj.pk)):
                self.assertEqual(self._put(kind, obj.pk).status_code, 400)

    def test_a_plain_post_is_not_a_resource(self):
        from post.models import Post

        plain = self._resource(kind=Post.KIND_POST)
        self.assertEqual(self._put("resource", plain.pk).status_code, 400)

    # ---- read re-checks the target every time ----
    def test_slot_goes_empty_when_the_target_stops_being_public(self):
        res = self._resource()
        self._put("resource", res.pk)
        self.assertIsNotNone(self.c.get(_show("feat_owner")).data["featured"])
        type(res).objects.filter(pk=res.pk).update(visibility="private")
        self.assertIsNone(self.c.get(_show("feat_owner")).data["featured"])
        type(res).objects.filter(pk=res.pk).update(visibility="public", is_deleted=True)
        self.assertIsNone(self.c.get(_show("feat_owner")).data["featured"])

    def test_deleted_class_makes_the_slot_empty(self):
        room = self._class()
        self._put("class", room.pk)
        type(room).objects.filter(pk=room.pk).update(is_deleted=True)
        self.assertIsNone(self.c.get(_show("feat_owner")).data["featured"])

    def test_a_broken_lookup_shows_no_card_not_an_error(self):
        self._put("test", self._test().pk)
        with mock.patch("user_profile.featured._lookup", side_effect=RuntimeError("boom")):
            r = self.c.get(_show("feat_owner"))
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data["featured"])

    # ---- visitors ----
    def test_visitor_sees_the_card(self):
        self._put("test", self._test().pk)
        self.c.force_authenticate(self.other)
        d = self.c.get(_show("feat_owner")).data
        self.assertEqual(d["featured"]["kind"], "test")

    def test_private_account_is_locked_for_a_non_follower_and_open_for_a_follower(self):
        self._put("test", self._test().pk)
        User.objects.filter(pk=self.owner.pk).update(is_private=True)
        self.c.force_authenticate(self.other)
        d = self.c.get(_show("feat_owner")).data
        self.assertTrue(d["locked"])
        self.assertIsNone(d["featured"])
        Follow.objects.create(follower=self.other, following=self.owner, status=Follow.Status.ACCEPTED)
        d = self.c.get(_show("feat_owner")).data
        self.assertFalse(d["locked"])
        self.assertIsNotNone(d["featured"])

    def test_blocked_is_404_and_unknown_user_is_404(self):
        self._put("test", self._test().pk)
        self.c.force_authenticate(self.other)
        with mock.patch("user_profile.featured_views.is_blocked_between", return_value=True):
            self.assertEqual(self.c.get(_show("feat_owner")).status_code, 404)
        self.assertEqual(self.c.get(_show("nobody_here")).status_code, 404)

    def test_only_the_owner_can_set_or_clear(self):
        # PUT / DELETE only ever act on request.user - another user's call cannot touch the owner's slot.
        self._put("test", self._test().pk)
        self.c.force_authenticate(self.other)
        self.assertEqual(self.c.delete(MINE).status_code, 204)
        self.assertEqual(ProfileFeatured.objects.filter(user=self.owner).count(), 1)

    def test_login_needed(self):
        anon = APIClient()
        self.assertIn(anon.get(_show("feat_owner")).status_code, (401, 403))
        self.assertIn(anon.put(MINE, {}, format="json").status_code, (401, 403))

    # ---- candidates ----
    def test_candidates_list_only_my_eligible_items_of_that_kind(self):
        from testseries.models import TestSeries

        mine = self._test(title="Mine")
        self._test(title="Draft", status=TestSeries.Status.DRAFT)
        TestSeries.objects.create(
            creator=self.other, title="Theirs", source=TestSeries.Source.INDIVIDUAL, status=TestSeries.Status.PUBLISHED,
        )
        r = self.c.get(CANDIDATES, {"kind": "test"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual([x["id"] for x in r.data["results"]], [str(mine.pk)])

    def test_candidates_for_resource_carry_file_count(self):
        self._resource()
        r = self.c.get(CANDIDATES, {"kind": "resource"})
        self.assertEqual(r.data["results"][0]["meta"], {"files": 0})

    def test_candidates_unknown_kind_is_400(self):
        self.assertEqual(self.c.get(CANDIDATES, {"kind": "zzz"}).status_code, 400)
        self.assertEqual(self.c.get(CANDIDATES).status_code, 400)

    def test_deleting_the_user_removes_the_slot(self):
        self._put("test", self._test().pk)
        self.owner.delete()
        self.assertFalse(ProfileFeatured.objects.exists())
