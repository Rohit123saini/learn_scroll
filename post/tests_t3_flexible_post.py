"""
T3 - New post: title and category are optional, the server fills them. Written by reading the code; NOT run yet.
Run: python manage.py test post.tests_t3_flexible_post
"""
import io
import shutil
import tempfile
from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from . import auto_title
from .models import Post

User = get_user_model()
_TMP_MEDIA = tempfile.mkdtemp(prefix="t3_media_")


def tearDownModule():
    shutil.rmtree(_TMP_MEDIA, ignore_errors=True)


def _png(name="a.png"):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (20, 90, 200)).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _mp4(name="clip.mp4"):
    # only the container signature: the create endpoint stores the file, ffmpeg work happens later in a task
    return SimpleUploadedFile(name, b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64, content_type="video/mp4")


def _pdf(name="Chapter 3 notes.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n", content_type="application/pdf")


# ----------------------------------------------------------------------------------------------------- pure helper
class AutoTitleTests(SimpleTestCase):
    def test_text_comes_first(self):
        self.assertEqual(auto_title.derive_title("Newton's laws in 5 minutes", ["a.png"]), "Newton's laws in 5 minutes")

    def test_text_is_cut_at_a_word_end_within_50_chars(self):
        t = auto_title.derive_title("word " * 30)
        self.assertLessEqual(len(t), auto_title.AUTO_TITLE_MAX + 1)  # + the ellipsis
        self.assertTrue(t.endswith("\u2026"))
        self.assertNotIn("  ", t)

    def test_markers_and_line_breaks_are_removed(self):
        self.assertEqual(auto_title.derive_title("**Algebra**\n\n_basics_ #maths"), "Algebra basics maths")

    def test_file_name_when_there_is_no_text(self):
        self.assertEqual(auto_title.derive_title("", ["Chapter_3-notes.pdf"]), "Chapter 3 notes")
        self.assertEqual(auto_title.derive_title(None, ["IMG_20261106_101530.jpg", "Physics sheet.png"]), "Physics sheet")

    def test_date_is_the_last_resort(self):
        self.assertEqual(auto_title.derive_title("", ["IMG_0042.jpg"], when=date(2026, 11, 6)), "Post - 6 Nov 2026")
        self.assertEqual(auto_title.derive_title("   ", [], when=date(2026, 1, 15)), "Post - 15 Jan 2026")

    def test_own_title_always_wins(self):
        self.assertEqual(auto_title.ensure_title("  My title ", "text", ["f.png"]), "My title")
        self.assertEqual(auto_title.ensure_title("", "text", ["f.png"]), "text")


# ----------------------------------------------------------------------------------------------------- POST /post/create/
@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class TitleLessPostTests(APITestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="t3_me", email="t3_me@example.com", password="pass12345")
        self.client.force_authenticate(self.me)

    def _create(self, **body):
        with self.captureOnCommitCallbacks(execute=False):
            return self.client.post(reverse("post-create"), body, format="multipart")

    def _only_post(self):
        return Post.objects.get(user=self.me)

    def test_image_post_without_title_or_category(self):
        r = self._create(post_type="image", content="Today's whiteboard after class", media_files=[_png("board.png")], media_types='["image"]')
        self.assertIn(r.status_code, (200, 201), r.content)
        post = self._only_post()
        self.assertEqual(post.title, "Today's whiteboard after class")
        self.assertEqual(post.category, "general")  # category left out -> model default

    def test_image_post_with_no_text_is_named_after_the_file(self):
        r = self._create(post_type="image", media_files=[_png("Organic chemistry map.png")], media_types='["image"]')
        self.assertIn(r.status_code, (200, 201), r.content)
        self.assertEqual(self._only_post().title, "Organic chemistry map")

    def test_camera_file_name_falls_back_to_the_date(self):
        r = self._create(post_type="image", media_files=[_png("IMG_20261106_101530.png")], media_types='["image"]')
        self.assertIn(r.status_code, (200, 201), r.content)
        self.assertTrue(self._only_post().title.startswith("Post - "))

    def test_video_post_without_title(self):
        r = self._create(post_type="video", media_files=[_mp4("Lecture 4 thermodynamics.mp4")], media_types='["video"]')
        self.assertIn(r.status_code, (200, 201), r.content)
        self.assertEqual(self._only_post().title, "Lecture 4 thermodynamics")

    def test_notes_pdf_without_title(self):
        r = self._create(post_type="document", content="", media_files=[_pdf("Chapter 3 notes.pdf")], media_types='["document"]')
        self.assertIn(r.status_code, (200, 201), r.content)
        self.assertEqual(self._only_post().title, "Chapter 3 notes")

    def test_own_title_is_kept(self):
        r = self._create(post_type="image", title="My title", content="caption", media_files=[_png()], media_types='["image"]')
        self.assertIn(r.status_code, (200, 201), r.content)
        self.assertEqual(self._only_post().title, "My title")

    def test_text_only_post_needs_no_title_and_gets_none(self):
        r = self._create(post_type="text", content="Just a thought")
        self.assertIn(r.status_code, (200, 201), r.content)
        post = self._only_post()
        self.assertFalse(post.title)
        self.assertEqual(post.category, "general")

    def test_mixed_files_are_accepted_as_a_carousel(self):
        r = self._create(post_type="mixed", content="photo + clip", media_files=[_png("a.png"), _mp4("b.mp4")],
                         media_types='["image","video"]')
        self.assertIn(r.status_code, (200, 201), r.content)
        self.assertEqual(self._only_post().post_type, "carousel")

    def test_unknown_category_is_still_refused(self):
        # optional is not the same as "anything goes": a category that IS sent must be a real one
        r = self._create(post_type="text", content="x", category="not-a-category")
        self.assertEqual(r.status_code, 400)

    def test_poll_still_needs_its_options(self):
        r = self._create(post_type="poll", content="Which one?")
        self.assertEqual(r.status_code, 400)

    def test_doubt_still_needs_its_question(self):
        r = self._create(post_type="doubt", content="")
        self.assertEqual(r.status_code, 400)


# ----------------------------------------------------------------------------------------------------- POST /post/resources/
@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class TitleLessResourceTests(APITestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="t3_res", email="t3_res@example.com", password="pass12345")
        self.client.force_authenticate(self.me)
        self.url = reverse("post-resources")

    def _create(self, files, **extra):
        body = {"subject": "Physics", **extra}
        body["files"] = files
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url, body, format="multipart")

    def test_resource_without_title_uses_the_description(self):
        r = self._create([_png("scan1.png")], content="Rotational motion short notes")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Post.objects.get(kind=Post.KIND_RESOURCE).title, "Rotational motion short notes")

    def test_resource_without_title_or_text_uses_the_file_name(self):
        r = self._create([_png("Kinematics formula sheet.png")])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Post.objects.get(kind=Post.KIND_RESOURCE).title, "Kinematics formula sheet")

    def test_resource_without_category_is_education(self):
        r = self._create([_png("a b.png")])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Post.objects.get(kind=Post.KIND_RESOURCE).category, "education")

    def test_unknown_category_falls_back_to_education(self):
        r = self._create([_png("a b.png")], category="whatever")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Post.objects.get(kind=Post.KIND_RESOURCE).category, "education")

    def test_a_too_long_title_is_still_refused(self):
        r = self._create([_png()], title="x" * 301)
        self.assertEqual(r.json()["code"], "title_too_long")

    def test_subject_and_file_are_still_required(self):
        self.assertEqual(self._create([_png()], subject="").json()["code"], "subject_required")
        r = self.client.post(self.url, {"subject": "s"}, format="multipart")
        self.assertEqual(r.json()["code"], "file_required")
