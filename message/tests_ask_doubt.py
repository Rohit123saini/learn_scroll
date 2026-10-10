# message/tests_ask_doubt.py
"""
Doubt Solver — photo input (Ask AI, `POST /message/ai/ask-doubt/`).

Pin karta hai:
  * `sniff_image_mime`: magic-bytes se JPEG/PNG/WEBP pehchaan, baaki sab reject.
  * `generate_doubt_answer`: image ho to Gemini ko (prompt, image Part) jaata
    hai, text-only ho to sirf prompt; cache key me image ka content-hash
    hota hai (alag photo => alag jawab, same photo => cache hit).
  * `AskAIDoubtView`: JSON (purana text flow) abhi bhi chalta hai; multipart
    photo ke saath question optional hai; bad format / oversize / koi bhi
    question-na-photo => 400; photo ka alag tighter throttle.

Gemini kabhi real call nahi hota — `_client` / `generate_doubt_answer` mock hain.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from . import ai_service
from .ai_service import sniff_image_mime, generate_doubt_answer

User = get_user_model()

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32


class SniffImageMimeTests(SimpleTestCase):
    def test_known_formats(self):
        self.assertEqual(sniff_image_mime(JPEG), "image/jpeg")
        self.assertEqual(sniff_image_mime(PNG), "image/png")
        self.assertEqual(sniff_image_mime(WEBP), "image/webp")

    def test_rejects_other_or_empty(self):
        self.assertIsNone(sniff_image_mime(GIF))
        self.assertIsNone(sniff_image_mime(b"%PDF-1.7 ..."))
        self.assertIsNone(sniff_image_mime(b""))
        self.assertIsNone(sniff_image_mime(None))


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class GenerateDoubtAnswerImageTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def _fake_client(self, text="Question: x\nStep 1 ...\nAnswer: 4"):
        client = mock.Mock()
        client.models.generate_content.return_value = _FakeResponse(text)
        return client

    def test_image_is_sent_to_gemini_with_prompt(self):
        client = self._fake_client()
        with mock.patch.object(ai_service, "AI_ENABLED", True), \
                mock.patch.object(ai_service, "_client", client):
            out = generate_doubt_answer(
                "", "", "general studies", "general:", image_bytes=PNG, image_mime="image/png",
            )
        self.assertIn("Answer:", out)
        contents = client.models.generate_content.call_args.kwargs["contents"]
        self.assertEqual(len(contents), 2)
        self.assertIn("STEP BY STEP", contents[0])
        # Part ke andar wahi bytes + mime gaya
        self.assertEqual(contents[1].inline_data.mime_type, "image/png")
        self.assertEqual(contents[1].inline_data.data, PNG)

    def test_text_only_path_unchanged(self):
        client = self._fake_client("Photosynthesis is ...")
        with mock.patch.object(ai_service, "AI_ENABLED", True), \
                mock.patch.object(ai_service, "_client", client):
            out = generate_doubt_answer("What is photosynthesis?", "", "general studies", "general:")
        self.assertEqual(out, "Photosynthesis is ...")
        contents = client.models.generate_content.call_args.kwargs["contents"]
        self.assertIsInstance(contents, str)  # sirf prompt, koi image Part nahi

    def test_cache_keyed_on_image_content(self):
        client = self._fake_client()
        kw = dict(question="solve", context_text="", context_label="g", cache_scope="general:",
                  image_mime="image/jpeg")
        with mock.patch.object(ai_service, "AI_ENABLED", True), \
                mock.patch.object(ai_service, "_client", client):
            generate_doubt_answer(image_bytes=JPEG, **kw)
            generate_doubt_answer(image_bytes=JPEG, **kw)       # same photo -> cache hit
            self.assertEqual(client.models.generate_content.call_count, 1)
            generate_doubt_answer(image_bytes=JPEG + b"1", **kw)  # alag photo -> naya call
            self.assertEqual(client.models.generate_content.call_count, 2)

    def test_empty_ai_reply_raises(self):
        client = self._fake_client("   ")
        with mock.patch.object(ai_service, "AI_ENABLED", True), \
                mock.patch.object(ai_service, "_client", client):
            with self.assertRaises(ValueError):
                generate_doubt_answer("q", "", "g", "general:", image_bytes=JPEG, image_mime="image/jpeg")


URL = "/message/ai/ask-doubt/"


def _photo(data=JPEG, name="q.jpg"):
    return SimpleUploadedFile(name, data, content_type="image/jpeg")


class AskAIDoubtViewPhotoTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="stud", password="x")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        p1 = mock.patch("message.views_ai.AI_ENABLED", True)
        p2 = mock.patch("message.views_ai.generate_doubt_answer", return_value="Step 1 ...\nAnswer: 4")
        p1.start(); self.addCleanup(p1.stop)
        self.gen = p2.start(); self.addCleanup(p2.stop)

    def test_json_text_flow_still_works(self):
        r = self.client.post(URL, {"question": "What is a mole?"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["used_image"])
        self.assertIsNone(self.gen.call_args.kwargs["image_bytes"])

    def test_photo_without_question_is_ok(self):
        r = self.client.post(URL, {"image": _photo()}, format="multipart")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["used_image"])
        kw = self.gen.call_args.kwargs
        self.assertEqual(kw["image_bytes"], JPEG)
        self.assertEqual(kw["image_mime"], "image/jpeg")
        self.assertEqual(kw["question"], "Solve this step by step.")

    def test_photo_with_question(self):
        r = self.client.post(URL, {"question": "step 3 samajh nahi aaya", "image": _photo()},
                             format="multipart")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.gen.call_args.kwargs["question"], "step 3 samajh nahi aaya")

    def test_neither_question_nor_photo_is_400(self):
        r = self.client.post(URL, {}, format="json")
        self.assertEqual(r.status_code, 400)
        self.gen.assert_not_called()

    def test_wrong_format_is_400_even_if_content_type_lies(self):
        fake = SimpleUploadedFile("q.jpg", GIF, content_type="image/jpeg")
        r = self.client.post(URL, {"image": fake}, format="multipart")
        self.assertEqual(r.status_code, 400)
        self.gen.assert_not_called()

    def test_oversize_photo_is_400(self):
        with mock.patch("message.views_ai.MAX_DOUBT_IMAGE_BYTES", 16):
            r = self.client.post(URL, {"image": _photo()}, format="multipart")
        self.assertEqual(r.status_code, 400)
        self.gen.assert_not_called()

    def test_ai_failure_is_500_not_leaky(self):
        self.gen.side_effect = RuntimeError("boom")
        r = self.client.post(URL, {"image": _photo()}, format="multipart")
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("boom", str(r.data))

    def test_photo_throttle_is_tighter_than_text(self):
        # 8/min photo bucket: 9th photo request 429, par text doubt abhi chalta rahe.
        for _ in range(8):
            self.assertEqual(
                self.client.post(URL, {"image": _photo()}, format="multipart").status_code, 200
            )
        self.assertEqual(
            self.client.post(URL, {"image": _photo()}, format="multipart").status_code, 429
        )
        self.assertEqual(
            self.client.post(URL, {"question": "text doubt"}, format="json").status_code, 200
        )

    def test_anonymous_is_rejected(self):
        r = APIClient().post(URL, {"question": "x"}, format="json")
        self.assertIn(r.status_code, (401, 403))
