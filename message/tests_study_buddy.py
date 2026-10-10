# message/tests_study_buddy.py
"""
AI Study Buddy — `POST /message/ai/study-buddy/` (explain | quiz | flashcards,
en | hi | hinglish, text and/or PDF).

Gemini kabhi real call nahi hota — `_client` / `generate_study_buddy` mock hain.
"""
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from . import ai_service
from .ai_service import generate_study_buddy, is_pdf, _clean_quiz, _clean_cards

User = get_user_model()

PDF = b"%PDF-1.7\n" + b"0" * 64
TEXT = "Photosynthesis is the process by which plants make food using sunlight."


class _Resp:
    def __init__(self, text):
        self.text = text


def _client(text):
    c = mock.Mock()
    c.models.generate_content.return_value = _Resp(text)
    return c


class CleanersTests(SimpleTestCase):
    def test_is_pdf(self):
        self.assertTrue(is_pdf(PDF))
        self.assertFalse(is_pdf(b"GIF89a"))
        self.assertFalse(is_pdf(b""))

    def test_quiz_letter_answer_mapped_to_option_text(self):
        out = _clean_quiz([{"question": "q", "options": ["a1", "b1", "c1", "d1"], "answer": "C"}])
        self.assertEqual(out[0]["answer"], "c1")

    def test_quiz_drops_malformed_items(self):
        out = _clean_quiz([
            {"question": "ok", "options": ["x", "y"], "answer": "x"},
            {"question": "", "options": ["x", "y"], "answer": "x"},          # no question
            {"question": "q", "options": ["x"], "answer": "x"},              # <2 options
            {"question": "q", "options": ["x", "y"], "answer": "zzz"},       # answer matches nothing
            "garbage",
        ])
        self.assertEqual(len(out), 1)

    def test_quiz_and_cards_capped_at_five(self):
        qs = [{"question": f"q{i}", "options": ["a", "b"], "answer": "a"} for i in range(9)]
        cs = [{"front": f"f{i}", "back": f"b{i}"} for i in range(9)]
        self.assertEqual(len(_clean_quiz(qs)), 5)
        self.assertEqual(len(_clean_cards(cs)), 5)

    def test_cards_drop_incomplete(self):
        self.assertEqual(_clean_cards([{"front": "f", "back": ""}, {"front": "f", "back": "b"}]),
                         [{"front": "f", "back": "b"}])


class GenerateStudyBuddyTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def _run(self, client, **kw):
        with mock.patch.object(ai_service, "AI_ENABLED", True), \
                mock.patch.object(ai_service, "_client", client):
            return generate_study_buddy(**kw)

    def test_explain_hindi_prompt_and_result(self):
        c = _client("Paudhe sooraj ki roshni se khana banate hain.")
        out = self._run(c, mode="explain", content=TEXT, language="hi")
        self.assertEqual(out, {"text": "Paudhe sooraj ki roshni se khana banate hain."})
        prompt = c.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("Devanagari", prompt)

    def test_quiz_json_with_fences_parsed(self):
        payload = {"questions": [{"question": "q?", "options": ["a", "b", "c", "d"], "answer": "B"}]}
        c = _client("```json\n" + json.dumps(payload) + "\n```")
        out = self._run(c, mode="quiz", content=TEXT)
        self.assertEqual(out["questions"][0]["answer"], "b")

    def test_flashcards(self):
        payload = {"cards": [{"front": f"f{i}", "back": f"b{i}"} for i in range(7)]}
        out = self._run(_client(json.dumps(payload)), mode="flashcards", content=TEXT, language="hinglish")
        self.assertEqual(len(out["cards"]), 5)

    def test_pdf_sent_as_part(self):
        c = _client("explained")
        self._run(c, mode="explain", content="", pdf_bytes=PDF)
        contents = c.models.generate_content.call_args.kwargs["contents"]
        self.assertEqual(contents[1].inline_data.mime_type, "application/pdf")
        self.assertEqual(contents[1].inline_data.data, PDF)

    def test_cache_per_mode_language_and_content(self):
        c = _client("x")
        self._run(c, mode="explain", content=TEXT, language="en")
        self._run(c, mode="explain", content=TEXT, language="en")          # hit
        self.assertEqual(c.models.generate_content.call_count, 1)
        self._run(c, mode="explain", content=TEXT, language="hi")          # alag language
        self._run(c, mode="explain", content=TEXT + "!", language="en")    # alag content
        self.assertEqual(c.models.generate_content.call_count, 3)

    def test_unusable_quiz_raises_and_is_not_cached(self):
        c = _client(json.dumps({"questions": [{"question": "q", "options": ["a"], "answer": "a"}]}))
        with self.assertRaises(ValueError):
            self._run(c, mode="quiz", content=TEXT)
        with self.assertRaises(ValueError):
            self._run(c, mode="quiz", content=TEXT)
        self.assertEqual(c.models.generate_content.call_count, 2)  # cache nahi hua

    def test_bad_mode_or_language(self):
        with self.assertRaises(ValueError):
            self._run(_client("x"), mode="nope", content=TEXT)
        with self.assertRaises(ValueError):
            self._run(_client("x"), mode="explain", content=TEXT, language="fr")


URL = "/message/ai/study-buddy/"


class StudyBuddyViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="stud2", password="x")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        p1 = mock.patch("message.views_ai.AI_ENABLED", True)
        p2 = mock.patch("message.views_ai.generate_study_buddy", return_value={"text": "hi"})
        p1.start(); self.addCleanup(p1.stop)
        self.gen = p2.start(); self.addCleanup(p2.stop)

    def test_text_explain(self):
        r = self.client.post(URL, {"mode": "explain", "content": TEXT, "language": "hi"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, {"mode": "explain", "language": "hi", "text": "hi"})
        kw = self.gen.call_args.kwargs
        self.assertEqual((kw["mode"], kw["language"], kw["pdf_bytes"]), ("explain", "hi", None))

    def test_language_defaults_to_en(self):
        self.client.post(URL, {"mode": "explain", "content": TEXT}, format="json")
        self.assertEqual(self.gen.call_args.kwargs["language"], "en")

    def test_bad_mode_and_language_400(self):
        self.assertEqual(self.client.post(URL, {"mode": "x", "content": TEXT}, format="json").status_code, 400)
        self.assertEqual(
            self.client.post(URL, {"mode": "quiz", "content": TEXT, "language": "fr"}, format="json").status_code, 400)

    def test_short_content_without_pdf_400(self):
        self.assertEqual(self.client.post(URL, {"mode": "quiz", "content": "hi"}, format="json").status_code, 400)
        self.gen.assert_not_called()

    def test_pdf_only_ok(self):
        f = SimpleUploadedFile("n.pdf", PDF, content_type="application/pdf")
        r = self.client.post(URL, {"mode": "flashcards", "pdf": f}, format="multipart")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.gen.call_args.kwargs["pdf_bytes"], PDF)

    def test_fake_pdf_and_oversize_400(self):
        fake = SimpleUploadedFile("n.pdf", b"GIF89a....", content_type="application/pdf")
        self.assertEqual(self.client.post(URL, {"mode": "quiz", "pdf": fake}, format="multipart").status_code, 400)
        big = SimpleUploadedFile("n.pdf", PDF, content_type="application/pdf")
        with mock.patch("message.views_ai.MAX_STUDY_PDF_BYTES", 8):
            self.assertEqual(self.client.post(URL, {"mode": "quiz", "pdf": big}, format="multipart").status_code, 400)
        self.gen.assert_not_called()

    def test_pdf_throttle_tighter_than_text(self):
        for _ in range(5):
            f = SimpleUploadedFile("n.pdf", PDF, content_type="application/pdf")
            self.assertEqual(self.client.post(URL, {"mode": "quiz", "pdf": f}, format="multipart").status_code, 200)
        f = SimpleUploadedFile("n.pdf", PDF, content_type="application/pdf")
        self.assertEqual(self.client.post(URL, {"mode": "quiz", "pdf": f}, format="multipart").status_code, 429)
        self.assertEqual(self.client.post(URL, {"mode": "quiz", "content": TEXT}, format="json").status_code, 200)

    def test_ai_failure_500_not_leaky(self):
        self.gen.side_effect = RuntimeError("boom")
        r = self.client.post(URL, {"mode": "explain", "content": TEXT}, format="json")
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("boom", str(r.data))

    def test_anonymous_rejected(self):
        self.assertIn(APIClient().post(URL, {"mode": "explain", "content": TEXT}, format="json").status_code, (401, 403))
