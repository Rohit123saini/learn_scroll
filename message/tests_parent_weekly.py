"""
TASK T2.5 — parent weekly progress card (`views_parent.build_weekly_progress`
and its 4 routes). Pillow is never needed: the PNG renderer is mocked.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from . import parent_card
from .models import ParentAccessCode, ParentToken

User = get_user_model()


@override_settings(APP_WEB_BASE_URL="https://learnscroll.test")
class ParentWeeklyProgressTests(TestCase):
    def setUp(self):
        self.student = User.objects.create(username="kid", first_name="Riya")
        self.client_s = APIClient()
        self.client_s.force_authenticate(self.student)

    def test_student_payload_and_whatsapp_link(self):
        r = self.client_s.get("/message/parent/weekly-progress/")
        self.assertEqual(r.status_code, 200)
        d = r.data
        for key in ("classes_attended", "tests_attempted", "homework", "streak_days", "share_text", "whatsapp_url", "label"):
            self.assertIn(key, d)
        self.assertTrue(d["share_text"].startswith("\U0001F4CA Mera LearnScroll weekly progress"))
        self.assertIn("/signup?ref=", d["share_text"])               # student shares their own referral link
        self.assertTrue(d["whatsapp_url"].startswith("https://wa.me/?text="))
        self.assertNotIn("\n", d["whatsapp_url"])                    # fully percent-encoded

    def test_requires_login(self):
        self.assertIn(APIClient().get("/message/parent/weekly-progress/").status_code, (401, 403))

    def _parent_client(self, status):
        code = ParentAccessCode.generate_for(self.student, label="Mom")
        token = ParentToken.objects.create(
            parent_access_code=code, token=ParentToken.generate_token(), status=status,
        )
        c = APIClient()
        c.credentials(HTTP_X_PARENT_TOKEN=token.token)
        return c, code

    def test_parent_side_needs_approved_token_and_never_leaks_code(self):
        pending, _ = self._parent_client(ParentToken.Status.PENDING)
        self.assertIn(pending.get("/message/parent/dashboard/weekly/").status_code, (401, 403))

        approved, code = self._parent_client(ParentToken.Status.APPROVED)
        r = approved.get("/message/parent/dashboard/weekly/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Riya ka LearnScroll weekly progress", r.data["share_text"])
        self.assertNotIn("ref=", r.data["share_text"])               # parent device doesn't mint a referral link
        self.assertNotIn(code.code, r.data["share_text"])            # access code never in the share text

    def test_card_png_and_501_fallback(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        with mock.patch("message.parent_card.render_png", return_value=png) as render:
            r = self.client_s.get("/message/parent/weekly-progress/card/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "image/png")
        self.assertEqual(r.content, png)
        rows = render.call_args.kwargs["rows"]
        self.assertEqual(rows[0][0], "Classes attended")

        with mock.patch("message.parent_card.render_png", side_effect=parent_card.ImageUnavailable("no pillow")):
            r = self.client_s.get("/message/parent/weekly-progress/card/")
        self.assertEqual(r.status_code, 501)
