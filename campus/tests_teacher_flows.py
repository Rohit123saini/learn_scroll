# campus/tests_teacher_flows.py
"""Teacher 2-tap flows: bulk attendance, bulk marks, title-only notice."""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import (
    AcademicSession, Attendance, Campus, ClassTeacherassigments, ExamTerm, Notice,
    ResultEntry, SchoolClass, Section, StaffProfile, StudentEnrollment, Subject,
)

User = get_user_model()


class TeacherFlowBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(username="tf_admin", password="x")
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
        self.other_section = Section.objects.create(school_class=self.klass, name="B")

        self.teacher = User.objects.create_user(username="tf_teacher", password="x")
        staff = StaffProfile.objects.create(
            campus=self.campus, user=self.teacher, role=StaffProfile.Role.CLASS_TEACHER
        )
        ClassTeacherassigments.objects.create(section=self.section, staff=staff)

        self.enrollments = []
        for i in range(4):
            u = User.objects.create_user(username=f"tf_stu{i}", password="x")
            self.enrollments.append(
                StudentEnrollment.objects.create(
                    student=u, section=self.section, session=self.session, roll_number=str(i + 1)
                )
            )
        self.subject = Subject.objects.create(campus=self.campus, name="Maths")
        self.term = ExamTerm.objects.create(
            session=self.session, name="Mid", start_date=date(2026, 9, 1), end_date=date(2026, 9, 10)
        )
        self.client.force_authenticate(self.teacher)


class BulkAttendanceTests(TeacherFlowBase):
    def _post(self, **kw):
        body = {"section": str(self.section.id), "date": timezone.localdate().isoformat(), "overrides": {}}
        body.update(kw)
        return self.client.post(reverse("attendance-bulk"), body, format="json")

    def test_all_present_with_one_absent(self):
        r = self._post(overrides={str(self.enrollments[1].id): "absent"})
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual((r.data["present"], r.data["absent"], r.data["created"]), (3, 1, 4))
        self.assertEqual(Attendance.objects.count(), 4)
        self.assertEqual(Attendance.objects.get(enrollment=self.enrollments[1]).status, "absent")

    def test_resave_updates_instead_of_duplicating_whole_day(self):
        # subject=None: DB unique constraint NULL ko equal nahi maanta — duplicate nahi banna chahiye.
        self._post()
        # Changing an already-marked row is an edit: needs a reason (campus/attendance_edit.py).
        r = self._post(overrides={str(self.enrollments[0].id): "late"}, reason="Bachcha late aaya tha")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(Attendance.objects.count(), 4)
        self.assertEqual((r.data["created"], r.data["updated"]), (0, 1))

    def test_client_request_id_replay_is_noop(self):
        first = self._post(client_request_id="abc-1", overrides={str(self.enrollments[0].id): "absent"})
        self.assertEqual(first.status_code, 200, first.data)
        # same id, even with different body (stale retry) -> original result, nothing rewritten
        again = self._post(client_request_id="abc-1")
        self.assertTrue(again.data.get("replayed"))
        self.assertEqual(Attendance.objects.get(enrollment=self.enrollments[0]).status, "absent")

    def test_clear_undoes_a_fresh_day(self):
        self._post()
        # Clearing a marked day is an edit too: the app's Undo sends reason="Undo".
        r = self._post(clear=True, reason="Undo")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["cleared"], 4)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_future_date_rejected(self):
        r = self._post(date=(timezone.localdate() + timedelta(days=1)).isoformat())
        self.assertEqual(r.status_code, 400)

    def test_unknown_status_and_foreign_enrollment_rejected(self):
        r = self._post(overrides={str(self.enrollments[0].id): "excused"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_teacher_of_other_section_forbidden(self):
        r = self._post(section=str(self.other_section.id))
        self.assertEqual(r.status_code, 403)

    def test_day_snapshot(self):
        self._post(overrides={str(self.enrollments[2].id): "absent"})
        r = self.client.get(
            reverse("attendance-day"),
            {"section": str(self.section.id), "date": timezone.localdate().isoformat()},
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["marked"])
        self.assertEqual(r.data["records"][str(self.enrollments[2].id)], "absent")
        self.assertEqual(len(r.data["records"]), 4)


class BulkResultsTests(TeacherFlowBase):
    def _post(self, rows, **kw):
        body = {
            "section": str(self.section.id), "subject": str(self.subject.id),
            "exam_term": str(self.term.id), "max_marks": 50, "rows": rows,
        }
        body.update(kw)
        return self.client.post(reverse("result-entry-bulk"), body, format="json")

    def test_upsert_and_skip_blank(self):
        e = self.enrollments
        r = self._post([
            {"enrollment": str(e[0].id), "marks_obtained": 41},
            {"enrollment": str(e[1].id), "marks_obtained": ""},
        ])
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["created"], r.data["skipped"]), (1, 1))
        # same marks again -> unchanged; new marks -> updated
        r = self._post([{"enrollment": str(e[0].id), "marks_obtained": 41}])
        self.assertEqual(r.data["unchanged"], 1)
        r = self._post([{"enrollment": str(e[0].id), "marks_obtained": 45}])
        self.assertEqual(r.data["updated"], 1)
        self.assertEqual(ResultEntry.objects.count(), 1)

    def test_all_or_nothing_on_bad_row(self):
        e = self.enrollments
        r = self._post([
            {"enrollment": str(e[0].id), "marks_obtained": 40},
            {"enrollment": str(e[1].id), "marks_obtained": 99},  # > max 50
        ])
        self.assertEqual(r.status_code, 400)
        self.assertIn(str(e[1].id), r.data["errors"]["rows"])
        self.assertEqual(ResultEntry.objects.count(), 0)

    def test_other_section_forbidden(self):
        r = self._post([], section=str(self.other_section.id))
        self.assertEqual(r.status_code, 403)


class TitleOnlyNoticeTests(TeacherFlowBase):
    def test_title_only_notice(self):
        r = self.client.post(
            reverse("notice-list"),
            {
                "campus": str(self.campus.id), "session": str(self.session.id),
                "section": str(self.section.id), "title": "Kal chhutti hai",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(Notice.objects.get().body, "")
