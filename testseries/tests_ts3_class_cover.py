# testseries/tests_ts3_class_cover.py
"""
TS3 (create-flow upgrade): put a new test series INSIDE one of the creator's classes, and give it a cover picture.

  * `POST /testseries/` with `classroom_id`  -> a class test: source=tuitionclass, context=that classroom, always free,
                                               no share & earn. Only a teacher / co-teacher / moderator of that class may.
  * `POST /testseries/{id}/cover/` (+ DELETE) -> cover picture, creator only, JPG/PNG/WEBP, 5 MB, real image only.
  * Old clients (no `classroom_id`) behave exactly as before.

Run:  python manage.py test testseries.tests_ts3_class_cover

WRITTEN BY READING THE CODE ONLY - nothing was run (no Django in the authoring sandbox). Treat the first run as the review.
Needs Pillow (already a project dependency: ImageField is used elsewhere).
"""
import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from tuitionclass.models import Classroom

from .models import TestSeries
from .tests_advanced import _client, _make_user, _mcq

_MEDIA = tempfile.mkdtemp(prefix="ts3_media_")


def _png_bytes(size=(8, 8)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


@override_settings(MEDIA_ROOT=_MEDIA)
class ClassLinkTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA, ignore_errors=True)

    def setUp(self):
        self.teacher = _make_user("teacher")
        self.other = _make_user("other")
        self.classroom = Classroom.objects.create(teacher=self.teacher, title="Maths Batch")

    def create(self, user, payload):
        return _client(user).post(reverse("testseries-list"), payload, format="json")

    # ---- class link ---------------------------------------------------------------------------------------------
    def test_teacher_creates_a_class_test_that_is_always_free(self):
        # The app sends is_paid false for a class test; even a client that asks for a price gets a free class test.
        res = self.create(self.teacher, {"title": "Weekly", "is_paid": True, "price_coins": 50, "classroom_id": self.classroom.pk})
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body["source"], "tuitionclass")
        self.assertEqual(body["context_type"], "classroom")
        self.assertFalse(body["is_paid"])
        self.assertEqual(body["price_coins"], 0)
        self.assertNotIn("classroom_id", body)  # write-only
        series = TestSeries.objects.get(pk=body["id"])
        self.assertEqual(series.context_id.int, self.classroom.pk)
        self.assertEqual(series.creator_id, self.teacher.id)

    def test_class_test_cannot_carry_share_and_earn(self):
        res = self.create(self.teacher, {
            "title": "x", "classroom_id": self.classroom.pk,
            "referral_enabled": True, "referral_commission_percent": 10,
        })
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("referral_enabled", res.json())

    def test_someone_who_does_not_teach_the_class_is_refused(self):
        res = self.create(self.other, {"title": "Sneaky", "classroom_id": self.classroom.pk})
        self.assertEqual(res.status_code, 403, res.content)
        self.assertFalse(TestSeries.objects.filter(title="Sneaky").exists())

    def test_a_class_that_does_not_exist_gives_the_same_refusal(self):
        res = self.create(self.teacher, {"title": "Ghost", "classroom_id": self.classroom.pk + 9999})
        self.assertEqual(res.status_code, 403, res.content)

    def test_classroom_id_must_be_a_positive_number(self):
        res = self.create(self.teacher, {"title": "x", "classroom_id": 0})
        self.assertEqual(res.status_code, 400, res.content)

    def test_old_clients_without_classroom_id_still_create_an_individual_series(self):
        res = self.create(self.teacher, {"title": "Solo", "is_paid": False, "price_coins": 0})
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["source"], "individual")
        self.assertIsNone(res.json()["context_type"])

    def test_classroom_id_cannot_be_added_by_a_later_patch(self):
        solo = self.create(self.teacher, {"title": "Solo", "is_paid": False, "price_coins": 0}).json()
        res = _client(self.teacher).patch(
            reverse("testseries-detail", kwargs={"pk": solo["id"]}), {"classroom_id": self.classroom.pk}, format="json"
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertEqual(TestSeries.objects.get(pk=solo["id"]).source, "individual")

    def test_class_test_publishes_free(self):
        body = self.create(self.teacher, {"title": "Weekly", "classroom_id": self.classroom.pk}).json()
        _mcq(TestSeries.objects.get(pk=body["id"]), 1)
        res = _client(self.teacher).post(reverse("testseries-publish", kwargs={"pk": body["id"]}))
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["status"], "published")
        self.assertFalse(res.json()["is_paid"])


@override_settings(MEDIA_ROOT=_MEDIA)
class CoverTests(TestCase):
    def setUp(self):
        self.owner = _make_user("owner")
        self.other = _make_user("other")
        body = _client(self.owner).post(
            reverse("testseries-list"), {"title": "With cover", "is_paid": False, "price_coins": 0}, format="json"
        ).json()
        self.sid = body["id"]
        self.url = reverse("testseries-cover", kwargs={"pk": self.sid})

    def upload(self, user, *, name="cover.png", data=None, content_type="image/png"):
        f = SimpleUploadedFile(name, data if data is not None else _png_bytes(), content_type=content_type)
        return _client(user).post(self.url, {"file": f}, format="multipart")

    def test_no_cover_means_empty_url(self):
        res = _client(self.owner).get(reverse("testseries-detail", kwargs={"pk": self.sid}))
        self.assertEqual(res.json()["cover_url"], "")

    def test_owner_sets_a_cover_and_it_shows_in_the_series(self):
        res = self.upload(self.owner)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIn("/testseries/covers/", res.json()["cover_url"])
        again = _client(self.owner).get(reverse("testseries-detail", kwargs={"pk": self.sid}))
        self.assertTrue(again.json()["cover_url"].startswith("http"))

    def test_replacing_a_cover_keeps_one_file(self):
        self.upload(self.owner)
        first = TestSeries.objects.get(pk=self.sid).cover_image.name
        self.upload(self.owner)
        second = TestSeries.objects.get(pk=self.sid).cover_image.name
        self.assertNotEqual(first, second)
        from django.core.files.storage import default_storage

        self.assertFalse(default_storage.exists(first))
        self.assertTrue(default_storage.exists(second))

    def test_owner_removes_the_cover(self):
        self.upload(self.owner)
        res = _client(self.owner).delete(self.url)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["cover_url"], "")
        self.assertFalse(TestSeries.objects.get(pk=self.sid).cover_image)

    def test_other_users_cannot_change_the_cover(self):
        # A draft is invisible to others (404); once published the series is visible but still creator-only (403).
        res = self.upload(self.other)
        self.assertIn(res.status_code, (403, 404))
        s = TestSeries.objects.get(pk=self.sid)
        _mcq(s, 1)
        _client(self.owner).post(reverse("testseries-publish", kwargs={"pk": self.sid}))
        self.assertEqual(self.upload(self.other).status_code, 403)
        self.assertEqual(_client(self.other).delete(self.url).status_code, 403)
        self.assertFalse(TestSeries.objects.get(pk=self.sid).cover_image)

    def test_cover_can_change_after_publishing(self):
        _mcq(TestSeries.objects.get(pk=self.sid), 1)
        _client(self.owner).post(reverse("testseries-publish", kwargs={"pk": self.sid}))
        self.assertEqual(self.upload(self.owner).status_code, 200)

    def test_wrong_extension_is_refused(self):
        res = self.upload(self.owner, name="cover.gif", content_type="image/gif")
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("file", res.json())

    def test_a_text_file_renamed_to_png_is_refused(self):
        res = self.upload(self.owner, data=b"this is not an image", name="fake.png")
        self.assertEqual(res.status_code, 400, res.content)
        self.assertFalse(TestSeries.objects.get(pk=self.sid).cover_image)

    def test_too_big_is_refused(self):
        big = _png_bytes() + b"\0" * (5 * 1024 * 1024 + 1)
        res = self.upload(self.owner, data=big)
        self.assertEqual(res.status_code, 400, res.content)

    def test_missing_file_is_refused(self):
        res = _client(self.owner).post(self.url, {}, format="multipart")
        self.assertEqual(res.status_code, 400, res.content)

    def test_public_share_preview_carries_the_cover(self):
        """The anonymous share-link preview (and the onboarding sample cards) show the cover too; '' when none."""
        _mcq(TestSeries.objects.get(pk=self.sid), 1)
        slug = _client(self.owner).post(reverse("testseries-publish", kwargs={"pk": self.sid})).json()["share_slug"]
        public = reverse("testseries-public", kwargs={"slug": slug})
        self.assertEqual(_client().get(public).json()["cover_url"], "")
        self.assertEqual(self.upload(self.owner).status_code, 200)
        self.assertIn("/testseries/covers/", _client().get(public).json()["cover_url"])
        self.assertNotIn("classroom_id", _client().get(public).json())
