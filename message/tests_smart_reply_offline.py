# message/tests_smart_reply_offline.py
"""
T0.4 — smart-reply (Gemini) offline / slow / disabled => chat ko kabhi 500 ya lag nahi.

Run:  python manage.py test message.tests_smart_reply_offline

Gemini ko kabhi real call nahi jaati: view ka `generate_reply_suggestions` patch hota
hai, aur timeout test me ek dheema fake client use hota hai.
"""
import time
from unittest import mock

from django.core.cache import cache
from django.test import override_settings

from . import ai_service
from .models import Message
from .tests_doubts import DoubtBase
from .views_ai import SmartReplySuggestionsView

URL = "/message/ai/smart-replies/"


class SmartReplyGracefulTests(DoubtBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        # bob ka message => alice (reply karne wali) ke liye suggestions maange ja sakte hain
        Message.objects.create(
            conversation_id=self.group.conversation_id, sender=self.bob, type="text", text="Kal test hai?",
        )
        p = mock.patch("message.views_ai.AI_ENABLED", True)
        p.start()
        self.addCleanup(p.stop)

    def post(self, user=None):
        return self.api(user or self.alice).post(
            URL, {"conversation_id": str(self.group.conversation_id)}, format="json",
        )

    def test_success_returns_suggestions(self):
        with mock.patch("message.views_ai.generate_reply_suggestions", return_value=["Haan", "Pata nahi", "Kyun?"]):
            r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["suggestions"], ["Haan", "Pata nahi", "Kyun?"])

    def test_gemini_offline_returns_200_empty_not_500(self):
        err = OSError("[Errno 11001] getaddrinfo failed")
        with mock.patch("message.views_ai.generate_reply_suggestions", side_effect=err):
            with self.assertLogs("message.views_ai", level="WARNING") as logs:
                r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["suggestions"], [])
        self.assertTrue(all(rec.levelname == "WARNING" for rec in logs.records))  # ERROR/CRITICAL nahi

    def test_backoff_skips_gemini_after_a_failure(self):
        with mock.patch("message.views_ai.generate_reply_suggestions", side_effect=OSError("offline")) as gen:
            self.post()
            r2 = self.post()
            self.assertEqual(gen.call_count, 1)  # dusri request Gemini tak gayi hi nahi
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.data["suggestions"], [])
        self.assertEqual(r2.data["reason"], "backoff")

    @override_settings(AI_SMART_REPLY_ENABLED=False)
    def test_feature_flag_off_returns_empty_without_calling_gemini(self):
        with mock.patch("message.views_ai.generate_reply_suggestions") as gen:
            r = self.post()
        gen.assert_not_called()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["suggestions"], [])
        self.assertEqual(r.data["reason"], "disabled")

    def test_non_member_still_gets_404(self):
        r = self.api(self.carol).post(URL, {"conversation_id": str(self.group.conversation_id)}, format="json")
        self.assertEqual(r.status_code, 404)


class GeminiTimeoutTests(DoubtBase):
    @override_settings(AI_SMART_REPLY_TIMEOUT=0.3)
    def test_slow_gemini_is_cut_off_at_the_deadline(self):
        class SlowModels:
            def generate_content(self, **kw):
                time.sleep(3)  # DNS/network atka hua
        fake = mock.Mock(models=SlowModels())
        started = time.monotonic()
        with mock.patch.object(ai_service, "_client", fake), mock.patch.object(ai_service, "AI_ENABLED", True):
            with self.assertRaises(ai_service.GeminiTimeout):
                ai_service.generate_reply_suggestions("Them: hi", "conv-1")
        self.assertLess(time.monotonic() - started, 2)  # 3s tak intezaar nahi kiya
