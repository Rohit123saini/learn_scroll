"""
post/tests_ai_study.py  (Feature 1 - AI summary / quiz / flashcards for a Notes/Resource post)

Run:  python manage.py test post.tests_ai_study

Notes
  * written by reading the code only - NOT executed when it was written
  * Gemini is never called: `message.ai_service.generate_study_buddy` and `AI_ENABLED` are patched
    (post/ai_study.py and the view import them at call time, so the patches take effect)
  * attachments are created straight through the ORM into a temp MEDIA_ROOT (fake PDF bytes are fine,
    the AI is mocked)
"""
import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from . import ai_study
from .models import Post, PostAiStudy, PostAttachment

User = get_user_model()

_TMP_MEDIA = tempfile.mkdtemp(prefix="f1_media_")
PDF = b"%PDF-1.7\n" + b"0" * 64
SUMMARY = {"text": "Newton's laws in short."}
QUIZ = {"questions": [{"question": "q", "options": ["a", "b", "c", "d"], "answer": "a"}]}
CAPTION = "Lecture notes on Newton's three laws of motion with solved examples."


def tearDownModule():
    shutil.rmtree(_TMP_MEDIA, ignore_errors=True)


def _user(name):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345")


def _resource(user, **kw):
    fields = dict(user=user, kind=Post.KIND_RESOURCE, post_type="document", title="Physics notes",
                  content=CAPTION, subject="Physics", level="Class 10", topics=["Motion"],
                  visibility="public", moderation_status="approved", download_allowed=True)
    fields.update(kw)
    return Post.objects.create(**fields)


def _attach(post, kind="pdf", name="notes.pdf", data=PDF, mime="application/pdf", size=None):
    att = PostAttachment(post=post, kind=kind, original_name=name, mime=mime,
                         size=len(data) if size is None else size)
    att.file.save(name, ContentFile(data), save=False)
    att.save()
    return att


def _fake_ai(mode, content="", language="en", pdf_bytes=None):
    return {"explain": SUMMARY, "quiz": QUIZ}.get(mode, {"cards": [{"front": "f", "back": "b"}]})


# --------------------------------------------------------------------------- pure helpers
class HashTests(SimpleTestCase):
    def test_hash_is_stable_and_sensitive(self):
        a = ai_study.source_hash("text")
        self.assertEqual(a, ai_study.source_hash("text"))
        self.assertNotEqual(a, ai_study.source_hash("text 2"))

    def test_hash_changes_with_attachment(self):
        att = mock.Mock(id="x", size=10)
        self.assertNotEqual(ai_study.source_hash("t", att, True), ai_study.source_hash("t", None, False))
        self.assertNotEqual(ai_study.source_hash("t", att, True),
                            ai_study.source_hash("t", mock.Mock(id="x", size=11), True))
        # file present but NOT used (downloads off for this viewer) == no file
        self.assertEqual(ai_study.source_hash("t", att, False), ai_study.source_hash("t", None, False))


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class SourceTests(APITestCase):
    def setUp(self):
        self.author = _user("author")
        self.student = _user("student")

    def test_post_text_has_title_caption_and_meta(self):
        t = ai_study.post_text(_resource(self.author))
        for part in ("Physics notes", "Newton", "Subject: Physics", "Level: Class 10", "Topics: Motion"):
            self.assertIn(part, t)

    def test_pdf_is_picked_over_txt(self):
        p = _resource(self.author)
        _attach(p, "doc", "a.txt", b"hello", "text/plain")
        pdf = _attach(p)
        self.assertEqual(ai_study.pick_attachment(p).id, pdf.id)

    def test_txt_text_is_appended(self):
        p = _resource(self.author, content="")
        _attach(p, "doc", "a.txt", b"Photosynthesis makes food from sunlight.", "text/plain")
        src = ai_study.build_source(p, self.student)
        self.assertIn("Photosynthesis", src.text)
        self.assertTrue(src.used_file)
        self.assertIsNone(src.pdf_bytes)

    def test_oversize_pdf_falls_back_to_text(self):
        p = _resource(self.author)
        _attach(p, size=50 * 1024 * 1024)
        src = ai_study.build_source(p, self.student)
        self.assertIsNone(src.pdf_bytes)
        self.assertFalse(src.used_file)

    def test_metadata_only_is_not_enough(self):
        p = _resource(self.author, title="", content="")
        _attach(p, "video", "a.mp4", b"x", "video/mp4")
        with self.assertRaises(ai_study.NotEnoughContent):
            ai_study.build_source(p, self.student)


# --------------------------------------------------------------------------- API
@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class PostAiStudyApiTests(APITestCase):
    def setUp(self):
        self.author = _user("author")
        self.student = _user("student")
        self.post = _resource(self.author)
        self.att = _attach(self.post)
        self.url = reverse("post-ai-study", kwargs={"id": self.post.id})
        self.client.force_authenticate(self.student)
        p1 = mock.patch("message.ai_service.AI_ENABLED", True)
        p2 = mock.patch("message.ai_service.generate_study_buddy", side_effect=_fake_ai)
        p1.start()
        self.ai = p2.start()
        self.addCleanup(mock.patch.stopall)

    def _post(self, **body):
        return self.client.post(self.url, {"mode": "summary", **body}, format="json")

    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertIn(self._post().status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_summary_reads_caption_and_pdf(self):
        r = self._post()
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["mode"], "summary")
        self.assertEqual(body["text"], SUMMARY["text"])
        self.assertFalse(body["cached"])
        kw = self.ai.call_args.kwargs
        self.assertEqual(kw["mode"], "explain")            # API "summary" -> ai_service "explain"
        self.assertIn("Newton", kw["content"])
        self.assertEqual(kw["pdf_bytes"], PDF)
        self.assertEqual(PostAiStudy.objects.count(), 1)

    def test_second_call_is_served_from_the_db(self):
        self._post()
        self.client.force_authenticate(_user("other"))      # a different student
        r = self._post()
        self.assertTrue(r.json()["cached"])
        self.assertEqual(self.ai.call_count, 1)
        self.assertEqual(PostAiStudy.objects.count(), 1)

    def test_quiz_flashcards_and_languages_are_separate_rows(self):
        self.assertEqual(self._post(mode="quiz").json()["questions"], QUIZ["questions"])
        self.assertIn("cards", self._post(mode="flashcards").json())
        self._post(language="hi")
        self._post(language="hinglish")
        self.assertEqual(PostAiStudy.objects.count(), 4)
        self.assertEqual(self.ai.call_count, 4)

    def test_editing_the_post_invalidates_the_cache(self):
        self._post()
        self.post.content = CAPTION + " Now with a chapter on friction and worked numericals."
        self.post.save(update_fields=["content"])
        self.assertFalse(self._post().json()["cached"])
        self.assertEqual(self.ai.call_count, 2)

    def test_swapping_the_file_invalidates_the_cache(self):
        self._post()
        self.att.delete()
        _attach(self.post, data=PDF + b"1" * 10)
        self.assertFalse(self._post().json()["cached"])

    def test_invalid_mode_and_language(self):
        self.assertEqual(self._post(mode="essay").json()["code"], "invalid_mode")
        self.assertEqual(self._post(language="fr").json()["code"], "invalid_language")
        self.assertEqual(self.ai.call_count, 0)

    def test_ai_disabled_is_503(self):
        with mock.patch("message.ai_service.AI_ENABLED", False):
            r = self._post()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["code"], "ai_unavailable")

    def test_hidden_or_deleted_post_is_404(self):
        self.post.visibility = "private"
        self.post.save(update_fields=["visibility"])
        self.assertEqual(self._post().status_code, 404)
        self.client.force_authenticate(self.author)         # the author still can
        self.assertEqual(self._post().status_code, 200)
        self.post.is_deleted = True
        self.post.save(update_fields=["is_deleted"])
        self.assertEqual(self._post().status_code, 404)

    def test_unapproved_post_is_404_for_others(self):
        self.post.moderation_status = "pending"
        self.post.save(update_fields=["moderation_status"])
        self.assertEqual(self._post().status_code, 404)

    def test_downloads_off_never_reads_the_file_for_students(self):
        self.post.download_allowed = False
        self.post.save(update_fields=["download_allowed"])
        r = self._post()                                      # caption is long enough -> text only
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(self.ai.call_args.kwargs["pdf_bytes"])

    def test_downloads_off_and_no_caption_is_403(self):
        self.post.download_allowed = False
        self.post.title, self.post.content = "", ""
        self.post.save(update_fields=["download_allowed", "title", "content"])
        r = self._post()
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "ai_not_allowed")
        self.assertEqual(self.ai.call_count, 0)

    def test_downloads_off_author_still_gets_the_file(self):
        self.post.download_allowed = False
        self.post.save(update_fields=["download_allowed"])
        self.client.force_authenticate(self.author)
        self._post()
        self.assertEqual(self.ai.call_args.kwargs["pdf_bytes"], PDF)

    def test_downloads_off_result_is_not_shared_with_file_result(self):
        self.client.force_authenticate(self.author)
        self._post()                                          # author: caption + PDF
        self.post.download_allowed = False
        self.post.save(update_fields=["download_allowed"])
        self.client.force_authenticate(self.student)
        r = self._post()                                      # student: caption only -> must NOT reuse
        self.assertFalse(r.json()["cached"])

    def test_nothing_readable_is_400(self):
        p = _resource(self.author, title="", content="")
        _attach(p, "image", "a.png", b"\x89PNG....", "image/png")
        r = self.client.post(reverse("post-ai-study", kwargs={"id": p.id}), {"mode": "summary"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "not_enough_content")

    def test_ai_failure_is_502_and_saves_nothing(self):
        self.ai.side_effect = RuntimeError("gemini down")
        r = self._post()
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["code"], "ai_failed")
        self.assertEqual(PostAiStudy.objects.count(), 0)
        self.ai.side_effect = _fake_ai                        # and it recovers on the next tap
        self.assertEqual(self._post().status_code, 200)

    def test_get_never_calls_the_ai(self):
        r = self.client.get(self.url, {"mode": "summary"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["available"])
        self.assertEqual(self.ai.call_count, 0)
        self._post()
        r = self.client.get(self.url, {"mode": "summary"})
        self.assertTrue(r.json()["available"])
        self.assertEqual(r.json()["text"], SUMMARY["text"])
        self.assertEqual(self.ai.call_count, 1)

    def test_get_validates_params_and_visibility(self):
        self.assertEqual(self.client.get(self.url, {"mode": "x"}).json()["code"], "invalid_mode")
        self.post.visibility = "private"
        self.post.save(update_fields=["visibility"])
        self.assertEqual(self.client.get(self.url, {"mode": "summary"}).status_code, 404)

    def test_throttle_applies_to_post_only(self):
        from .ai_study_views import PostAiStudyAPIView
        view = PostAiStudyAPIView()
        view.request = mock.Mock(method="GET")
        self.assertEqual(view.get_throttles(), [])
        view.request = mock.Mock(method="POST")
        self.assertEqual(len(view.get_throttles()), 1)
