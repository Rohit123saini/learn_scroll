# campus/tests_pilot.py
"""T2.8 (Loop 6) — invite-flow polish + pilot-kit checklist."""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from user_profile.models import UserBadge

from .models import (
    AcademicSession, Campus, CampusInviteCode, SchoolClass, Section, StaffProfile, StudentEnrollment, Subject,
)

User = get_user_model()


class PilotBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(username="pilot_admin", password="x")
        self.campus = Campus.objects.create(
            created_by=self.admin, name="Green Valley", type="school",
            verification_status=Campus.VerificationStatus.APPROVED,
        )
        StaffProfile.objects.create(campus=self.campus, user=self.admin, role=StaffProfile.Role.ADMIN)
        self.session = AcademicSession.objects.create(
            campus=self.campus, name="2026-27", start_date=date(2026, 6, 1),
            end_date=date(2027, 4, 30), is_current=True,
        )
        self.klass = SchoolClass.objects.create(campus=self.campus, session=self.session, name="Class 10")
        self.section = Section.objects.create(school_class=self.klass, name="A")
        self.student = User.objects.create_user(username="pilot_student", password="x")

    def _code(self, **kw):
        return CampusInviteCode.generate_for(
            section=self.section, campus=self.campus, created_by=self.admin, **kw
        )

    def _redeem(self, user, code):
        self.client.force_authenticate(user)
        return self.client.post(reverse("campus-invite-code-redeem"), {"code": code}, format="json")


class InviteFlowPolishTests(PilotBase):
    def test_messy_pasted_code_is_normalised(self):
        code = self._code().code
        messy = f" {code[:3].lower()}-{code[3:]} . "
        resp = self._redeem(self.student, messy)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["section_label"], "Class 10 - A")
        self.assertEqual(resp.data["class_name"], "Class 10")
        self.assertFalse(resp.data["already_enrolled"])

    def test_join_earns_founding_badge_once(self):
        code = self._code().code
        resp = self._redeem(self.student, code)
        self.assertEqual(resp.data["welcome_badge"], "founding_member")
        self.assertTrue(UserBadge.objects.filter(user=self.student, badge__code="founding_member").exists())
        again = self._redeem(self.student, code)
        self.assertEqual(again.status_code, status.HTTP_200_OK)
        self.assertTrue(again.data["already_enrolled"])
        self.assertIsNone(again.data["welcome_badge"])

    def test_specific_reasons_for_unusable_codes(self):
        revoked = self._code()
        CampusInviteCode.objects.filter(pk=revoked.pk).update(is_active=False)
        resp = self._redeem(self.student, revoked.code)
        self.assertEqual((resp.status_code, resp.data["reason"]), (400, "revoked"))

        expired = self._code()
        CampusInviteCode.objects.filter(pk=expired.pk).update(expires_at=timezone.now() - timedelta(days=1))
        self.assertEqual(self._redeem(self.student, expired.code).data["reason"], "expired")

        full = self._code(max_uses=1)
        CampusInviteCode.objects.filter(pk=full.pk).update(uses_count=1)
        self.assertEqual(self._redeem(self.student, full.code).data["reason"], "full")

        self.assertEqual(self._redeem(self.student, "ZZZZZZZ").data["reason"], "invalid")
        self.assertEqual(self._redeem(self.student, "  --  ").data["reason"], "missing")

    def test_enrolled_student_is_not_locked_out_when_code_fills_up(self):
        code = self._code(max_uses=1)
        self.assertEqual(self._redeem(self.student, code.code).status_code, 201)
        resp = self._redeem(self.student, code.code)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["already_enrolled"])

    def test_generate_returns_remaining_uses_and_whatsapp_link(self):
        self.client.force_authenticate(self.admin)
        url = reverse("campus-invite-code-generate", args=[self.campus.pk, self.section.pk])
        resp = self.client.post(url, {"max_uses": 3}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["remaining_uses"], 3)
        self.assertEqual(resp.data["unavailable_reason"], "")
        self.assertTrue(resp.data["whatsapp_url"].startswith("https://wa.me/?text="))
        self.assertIn(resp.data["code"], resp.data["whatsapp_url"])


class PilotKitTests(PilotBase):
    def _kit(self, user):
        self.client.force_authenticate(user)
        return self.client.get(reverse("campus-pilot-kit", args=[self.campus.pk]))

    def test_non_admin_is_forbidden(self):
        self.assertEqual(self._kit(self.student).status_code, 403)

    def test_progress_tracks_real_setup(self):
        data = self._kit(self.admin).data
        done = {s["key"] for s in data["steps"] if s["done"]}
        self.assertEqual(done, {"verified", "session", "sections"})
        self.assertEqual(data["next_step"], "subjects")
        self.assertEqual(data["completed"], 3)
        self.assertEqual(data["percent"], int(round(100 * 3 / data["total"])))

        Subject.objects.create(campus=self.campus, name="Maths")
        code = self._code()
        self._redeem(self.student, code.code)
        self.client.force_authenticate(self.admin)
        data = self.client.get(reverse("campus-pilot-kit", args=[self.campus.pk])).data
        by_key = {s["key"]: s for s in data["steps"]}
        self.assertTrue(by_key["subjects"]["done"])
        self.assertTrue(by_key["invite_code"]["done"])
        self.assertEqual(by_key["students"]["count"], 1)
        self.assertFalse(by_key["students"]["done"])
        self.assertEqual(data["students_joined_via_code"], 1)
