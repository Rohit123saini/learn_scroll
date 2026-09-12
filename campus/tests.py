# campus/tests.py
"""
Covers Phase 1-3 (campus creation auto-provisioning an admin,
one-current-session-per-campus, structural scoping validation, the
section-group bridge call, the subject-teacher approval flow, and
campus-visibility scoping) plus Phase 4-8 (live sessions, timetable
clash-detection, attendance + summary, assignments + grading,
syllabus progress, results + report card, fee module, digital ID
cards, and parent-link verification).

Assumes `campus.urls` is included in the project's root urlconf (e.g.
`path('campus/', include('campus.urls'))`) — same as every other app's
`urls.py` in this project. If it isn't wired in yet, `reverse(...)`
calls below will raise `NoReverseMatch` until it is.
"""
from datetime import date, time, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import (
    AcademicSession,
    Assignment,
    AssignmentSubmission,
    Attendance,
    Campus,
    CampusLiveSession,
    Department,
    DigitalIDCard,
    ExamTerm,
    FeeInvoice,
    FeePayment,
    FeeStructure,
    Notice,
    ResultEntry,
    Room,
    SchoolClass,
    Section,
    StaffProfile,
    StudentEnrollment,
    Subject,
    SubjectTeacherAssignment,
    SyllabusProgress,
    SyllabusUnit,
    TimeSlot,
    TimetableEntry,
)
from user_profile.models import CoinLedger

User = get_user_model()


def make_session(campus, name="2026-27", is_current=True):
    return AcademicSession.objects.create(
        campus=campus,
        name=name,
        start_date=date(2026, 6, 1),
        end_date=date(2027, 4, 30),
        is_current=is_current,
    )


class CampusCreationTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.client.force_authenticate(user=self.alice)

    def test_creating_campus_auto_provisions_admin_staff_profile(self):
        response = self.client.post(reverse("campus-list"), {"name": "Green Valley School", "type": "school"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        campus = Campus.objects.get(id=response.data["id"])
        self.assertEqual(campus.created_by, self.alice)
        profile = StaffProfile.objects.get(campus=campus, user=self.alice)
        self.assertEqual(profile.role, StaffProfile.Role.ADMIN)

    def test_non_member_cannot_see_someone_elses_campus(self):
        bob = User.objects.create_user(username="bob", password="pass12345")
        campus = Campus.objects.create(name="Bob's School", created_by=bob)
        StaffProfile.objects.create(campus=campus, user=bob, role=StaffProfile.Role.ADMIN)

        response = self.client.get(reverse("campus-list"))
        campus_ids = [c["id"] for c in response.data.get("results", response.data)]
        self.assertNotIn(str(campus.id), campus_ids)

    def test_member_via_enrollment_can_see_campus(self):
        bob = User.objects.create_user(username="bob", password="pass12345")
        campus = Campus.objects.create(name="Bob's School", created_by=bob)
        session = make_session(campus)
        school_class = SchoolClass.objects.create(campus=campus, session=session, name="Class 10")
        section = Section.objects.create(school_class=school_class, name="A")
        StudentEnrollment.objects.create(student=self.alice, section=section, session=session)

        response = self.client.get(reverse("campus-list"))
        campus_ids = [c["id"] for c in response.data.get("results", response.data)]
        self.assertIn(str(campus.id), campus_ids)


class AcademicSessionTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.client.force_authenticate(user=self.alice)
        self.campus = Campus.objects.create(name="Green Valley", created_by=self.alice)
        StaffProfile.objects.create(campus=self.campus, user=self.alice, role=StaffProfile.Role.ADMIN)

    def test_only_one_current_session_per_campus(self):
        s1 = make_session(self.campus, name="2025-26", is_current=True)
        s2 = make_session(self.campus, name="2026-27", is_current=True)

        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertFalse(s1.is_current)
        self.assertTrue(s2.is_current)

    def test_set_current_action_flips_previous_current_off(self):
        s1 = make_session(self.campus, name="2025-26", is_current=True)
        s2 = make_session(self.campus, name="2026-27", is_current=False)

        response = self.client.post(reverse("academic-session-set-current", args=[s2.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertFalse(s1.is_current)
        self.assertTrue(s2.is_current)

    def test_non_admin_cannot_set_current_session(self):
        outsider = User.objects.create_user(username="carol", password="pass12345")
        self.client.force_authenticate(user=outsider)
        session = make_session(self.campus, is_current=False)

        response = self.client.post(reverse("academic-session-set-current", args=[session.id]))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class StructuralScopingValidationTests(APITestCase):
    """`SchoolClass`/`Subject`/`Notice` validation that a related object
    (department/session/section) actually belongs to the same campus."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.client.force_authenticate(user=self.alice)
        self.campus_a = Campus.objects.create(name="Campus A", created_by=self.alice)
        self.campus_b = Campus.objects.create(name="Campus B", created_by=self.alice)
        StaffProfile.objects.create(campus=self.campus_a, user=self.alice, role=StaffProfile.Role.ADMIN)
        StaffProfile.objects.create(campus=self.campus_b, user=self.alice, role=StaffProfile.Role.ADMIN)
        self.session_a = make_session(self.campus_a)

    def test_school_class_rejects_session_from_different_campus(self):
        response = self.client.post(
            reverse("school-class-list"),
            {"campus": str(self.campus_b.id), "session": str(self.session_a.id), "name": "Class 10"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("session", response.data)

    def test_school_class_accepts_matching_campus_and_session(self):
        response = self.client.post(
            reverse("school-class-list"),
            {"campus": str(self.campus_a.id), "session": str(self.session_a.id), "name": "Class 10"},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class SectionGroupBridgeTests(APITestCase):
    """Section creation should call `campus.bridge.create_section_group` —
    verified via mock, since the real `core.classroom_chat_bridge` isn't
    available to actually create a `message.Group` in this test run."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.client.force_authenticate(user=self.alice)
        self.campus = Campus.objects.create(name="Green Valley", created_by=self.alice)
        StaffProfile.objects.create(campus=self.campus, user=self.alice, role=StaffProfile.Role.ADMIN)
        self.session = make_session(self.campus)
        self.school_class = SchoolClass.objects.create(campus=self.campus, session=self.session, name="Class 10")

    def test_creating_section_calls_bridge_with_the_new_section(self):
        with mock.patch("campus.bridge.create_section_group") as mocked:
            response = self.client.post(
                reverse("section-list"), {"school_class": str(self.school_class.id), "name": "A"}
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mocked.assert_called_once()
        called_section = mocked.call_args.args[0]
        self.assertEqual(str(called_section.id), response.data["id"])
        self.assertEqual(mocked.call_args.kwargs["actor"], self.alice)

    def test_bridge_not_being_wired_up_yet_does_not_break_section_creation(self):
        # No mock here — exercises the REAL `campus.bridge.create_section_
        # group`, which should degrade to a logged no-op (core app not
        # installed in this test project) rather than raising.
        response = self.client.post(
            reverse("section-list"), {"school_class": str(self.school_class.id), "name": "B"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class SubjectTeacherApprovalFlowTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="pass12345")
        self.class_teacher_user = User.objects.create_user(username="teacher1", password="pass12345")
        self.subject_teacher_user = User.objects.create_user(username="teacher2", password="pass12345")
        self.campus = Campus.objects.create(name="Green Valley", created_by=self.admin)
        StaffProfile.objects.create(campus=self.campus, user=self.admin, role=StaffProfile.Role.ADMIN)
        self.class_teacher_staff = StaffProfile.objects.create(
            campus=self.campus, user=self.class_teacher_user, role=StaffProfile.Role.CLASS_TEACHER
        )
        self.subject_teacher_staff = StaffProfile.objects.create(
            campus=self.campus, user=self.subject_teacher_user, role=StaffProfile.Role.SUBJECT_TEACHER
        )
        self.session = make_session(self.campus)
        self.school_class = SchoolClass.objects.create(campus=self.campus, session=self.session, name="Class 10")
        self.section = Section.objects.create(school_class=self.school_class, name="A")
        from .models import ClassTeacherAssignment
        ClassTeacherAssignment.objects.create(section=self.section, staff=self.class_teacher_staff)
        self.subject = Subject.objects.create(campus=self.campus, name="Mathematics")

    def _create_pending_request(self):
        return SubjectTeacherAssignment.objects.create(
            section=self.section, subject=self.subject, staff=self.subject_teacher_staff
        )

    def test_class_teacher_can_approve(self):
        assignment = self._create_pending_request()
        self.client.force_authenticate(user=self.class_teacher_user)
        response = self.client.post(reverse("subject-teacher-assignment-approve", args=[assignment.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        assignment.refresh_from_db()
        self.assertEqual(assignment.status, SubjectTeacherAssignment.Status.APPROVED)
        self.assertEqual(assignment.approved_by, self.class_teacher_staff)
        self.assertIsNotNone(assignment.responded_at)

    def test_class_teacher_can_reject(self):
        assignment = self._create_pending_request()
        self.client.force_authenticate(user=self.class_teacher_user)
        response = self.client.post(reverse("subject-teacher-assignment-reject", args=[assignment.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        assignment.refresh_from_db()
        self.assertEqual(assignment.status, SubjectTeacherAssignment.Status.REJECTED)

    def test_unrelated_subject_teacher_cannot_approve(self):
        assignment = self._create_pending_request()
        outsider = User.objects.create_user(username="outsider", password="pass12345")
        StaffProfile.objects.create(campus=self.campus, user=outsider, role=StaffProfile.Role.SUBJECT_TEACHER)
        self.client.force_authenticate(user=outsider)

        response = self.client.post(reverse("subject-teacher-assignment-approve", args=[assignment.id]))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, SubjectTeacherAssignment.Status.PENDING)

    def test_campus_admin_can_also_approve_as_fallback(self):
        assignment = self._create_pending_request()
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(reverse("subject-teacher-assignment-approve", args=[assignment.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class NoticePermissionTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="pass12345")
        self.campus = Campus.objects.create(name="Green Valley", created_by=self.admin)
        StaffProfile.objects.create(campus=self.campus, user=self.admin, role=StaffProfile.Role.ADMIN)
        self.session = make_session(self.campus)

    def test_staff_can_post_campus_wide_notice(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            reverse("notice-list"),
            {
                "campus": str(self.campus.id),
                "session": str(self.session.id),
                "title": "Holiday",
                "body": "School closed tomorrow.",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Notice.objects.get(id=response.data["id"]).posted_by, self.admin)

    def test_non_staff_cannot_post_notice(self):
        outsider = User.objects.create_user(username="outsider", password="pass12345")
        self.client.force_authenticate(user=outsider)
        response = self.client.post(
            reverse("notice-list"),
            {
                "campus": str(self.campus.id),
                "session": str(self.session.id),
                "title": "Spam",
                "body": "Not allowed.",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_notice_rejects_section_from_a_different_campus(self):
        other_campus = Campus.objects.create(name="Other", created_by=self.admin)
        other_session = make_session(other_campus, name="X")
        other_class = SchoolClass.objects.create(campus=other_campus, session=other_session, name="Class 1")
        other_section = Section.objects.create(school_class=other_class, name="A")

        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            reverse("notice-list"),
            {
                "campus": str(self.campus.id),
                "session": str(self.session.id),
                "section": str(other_section.id),
                "title": "Wrong scope",
                "body": "Should be rejected.",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class StudentEnrollmentValidationTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="pass12345")
        self.student = User.objects.create_user(username="student1", password="pass12345")
        self.campus = Campus.objects.create(name="Green Valley", created_by=self.admin)
        StaffProfile.objects.create(campus=self.campus, user=self.admin, role=StaffProfile.Role.ADMIN)
        self.session = make_session(self.campus)
        self.school_class = SchoolClass.objects.create(campus=self.campus, session=self.session, name="Class 10")
        self.section = Section.objects.create(school_class=self.school_class, name="A")
        self.client.force_authenticate(user=self.admin)

    def test_enrolling_with_mismatched_session_is_rejected(self):
        other_session = make_session(self.campus, name="2024-25", is_current=False)
        response = self.client.post(
            reverse("student-enrollment-list"),
            {
                "student": str(self.student.id),
                "section": str(self.section.id),
                "session": str(other_session.id),
                "roll_number": "10",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_valid_enrollment_succeeds(self):
        response = self.client.post(
            reverse("student-enrollment-list"),
            {
                "student": str(self.student.id),
                "section": str(self.section.id),
                "session": str(self.session.id),
                "roll_number": "10",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            StudentEnrollment.objects.filter(student=self.student, section=self.section).exists()
        )

def make_full_fixture(test_case):
    """
    Shared fixture for Phase 4-8 tests: a campus with one current
    session, one class/section, one subject, an admin, an approved
    subject-teacher, and an enrolled student. Attaches everything onto
    `test_case` as attributes so each test class's setUp stays short.
    """
    test_case.admin = User.objects.create_user(username="admin", password="pass12345")
    test_case.teacher_user = User.objects.create_user(username="teacher1", password="pass12345")
    test_case.student_user = User.objects.create_user(username="student1", password="pass12345")
    test_case.outsider = User.objects.create_user(username="outsider", password="pass12345")

    test_case.campus = Campus.objects.create(name="Green Valley", created_by=test_case.admin)
    StaffProfile.objects.create(campus=test_case.campus, user=test_case.admin, role=StaffProfile.Role.ADMIN)
    test_case.teacher_staff = StaffProfile.objects.create(
        campus=test_case.campus, user=test_case.teacher_user, role=StaffProfile.Role.SUBJECT_TEACHER
    )
    test_case.session = make_session(test_case.campus)
    test_case.school_class = SchoolClass.objects.create(
        campus=test_case.campus, session=test_case.session, name="Class 10"
    )
    test_case.section = Section.objects.create(school_class=test_case.school_class, name="A")
    test_case.subject = Subject.objects.create(campus=test_case.campus, name="Mathematics")
    SubjectTeacherAssignment.objects.create(
        section=test_case.section,
        subject=test_case.subject,
        staff=test_case.teacher_staff,
        status=SubjectTeacherAssignment.Status.APPROVED,
    )
    test_case.enrollment = StudentEnrollment.objects.create(
        student=test_case.student_user, section=test_case.section, session=test_case.session, roll_number="1"
    )


class CampusLiveSessionTests(APITestCase):
    def setUp(self):
        make_full_fixture(self)
        self.client.force_authenticate(user=self.teacher_user)

    def test_approved_subject_teacher_can_schedule_session(self):
        with mock.patch("campus.bridge.provision_video_room", return_value="room-123") as provision, \
                mock.patch("campus.bridge.notify") as notify:
            response = self.client.post(
                reverse("campus-live-session-list"),
                {
                    "section": str(self.section.id),
                    "subject": str(self.subject.id),
                    "teacher": str(self.teacher_staff.id),
                    "scheduled_at": timezone.now().isoformat(),
                },
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        provision.assert_called_once()
        notify.assert_called_once()
        session_obj = CampusLiveSession.objects.get(id=response.data["id"])
        self.assertEqual(session_obj.room_id, "room-123")
        self.assertTrue(Notice.objects.filter(section=self.section, title__icontains="scheduled").exists())

    def test_unrelated_subject_teacher_cannot_schedule(self):
        other_teacher = User.objects.create_user(username="teacher2", password="pass12345")
        StaffProfile.objects.create(campus=self.campus, user=other_teacher, role=StaffProfile.Role.SUBJECT_TEACHER)
        self.client.force_authenticate(user=other_teacher)
        response = self.client.post(
            reverse("campus-live-session-list"),
            {
                "section": str(self.section.id),
                "subject": str(self.subject.id),
                "teacher": str(self.teacher_staff.id),
                "scheduled_at": timezone.now().isoformat(),
            },
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_start_action_flips_status_and_notifies(self):
        live_session = CampusLiveSession.objects.create(
            section=self.section, subject=self.subject, teacher=self.teacher_staff, scheduled_at=timezone.now()
        )
        with mock.patch("campus.bridge.notify") as notify:
            response = self.client.post(reverse("campus-live-session-start", args=[live_session.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        live_session.refresh_from_db()
        self.assertEqual(live_session.status, CampusLiveSession.Status.LIVE)
        notify.assert_called_once()


class TimetableClashDetectionTests(APITestCase):
    def setUp(self):
        make_full_fixture(self)
        self.client.force_authenticate(user=self.admin)
        self.time_slot = TimeSlot.objects.create(
            campus=self.campus, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0), label="Period 1"
        )
        self.room = Room.objects.create(campus=self.campus, name="Room 1")

    def test_first_entry_succeeds(self):
        response = self.client.post(
            reverse("timetable-entry-list"),
            {
                "section": str(self.section.id),
                "subject": str(self.subject.id),
                "staff": str(self.teacher_staff.id),
                "time_slot": str(self.time_slot.id),
                "room": str(self.room.id),
                "session": str(self.session.id),
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_same_staff_same_slot_clash_is_rejected(self):
        TimetableEntry.objects.create(
            section=self.section, subject=self.subject, staff=self.teacher_staff,
            time_slot=self.time_slot, session=self.session,
        )
        other_class = SchoolClass.objects.create(campus=self.campus, session=self.session, name="Class 9")
        other_section = Section.objects.create(school_class=other_class, name="A")
        response = self.client.post(
            reverse("timetable-entry-list"),
            {
                "section": str(other_section.id),
                "subject": str(self.subject.id),
                "staff": str(self.teacher_staff.id),
                "time_slot": str(self.time_slot.id),
                "session": str(self.session.id),
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class AttendanceTests(APITestCase):
    def setUp(self):
        make_full_fixture(self)

    def test_subject_teacher_can_mark_attendance(self):
        self.client.force_authenticate(user=self.teacher_user)
        response = self.client.post(
            reverse("attendance-list"),
            {
                "enrollment": str(self.enrollment.id),
                "date": str(date.today()),
                "subject": str(self.subject.id),
                "status": "present",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        record = Attendance.objects.get(id=response.data["id"])
        self.assertEqual(record.marked_by, self.teacher_user)

    def test_outsider_cannot_mark_attendance(self):
        self.client.force_authenticate(user=self.outsider)
        response = self.client.post(
            reverse("attendance-list"),
            {
                "enrollment": str(self.enrollment.id),
                "date": str(date.today()),
                "subject": str(self.subject.id),
                "status": "present",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_summary_percent_reflects_marked_records(self):
        Attendance.objects.create(enrollment=self.enrollment, date=date(2026, 6, 2), status=Attendance.Status.PRESENT)
        Attendance.objects.create(enrollment=self.enrollment, date=date(2026, 6, 3), status=Attendance.Status.ABSENT)
        self.client.force_authenticate(user=self.student_user)
        response = self.client.get(reverse("attendance-summary"), {"enrollment": str(self.enrollment.id)})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total"], 2)
        self.assertEqual(response.data["percent"], 50.0)


class AssignmentFlowTests(APITestCase):
    def setUp(self):
        make_full_fixture(self)

    def test_posting_assignment_precreates_submissions_and_notifies(self):
        self.client.force_authenticate(user=self.teacher_user)
        with mock.patch("campus.bridge.notify") as notify:
            response = self.client.post(
                reverse("assignment-list"),
                {
                    "section": str(self.section.id),
                    "subject": str(self.subject.id),
                    "title": "Chapter 1 problems",
                    "due_date": str(date.today() + timedelta(days=7)),
                    "session": str(self.session.id),
                },
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        notify.assert_called_once()
        assignment = Assignment.objects.get(id=response.data["id"])
        submission = AssignmentSubmission.objects.get(assignment=assignment, student=self.student_user)
        self.assertEqual(submission.status, AssignmentSubmission.Status.MISSING)

    def test_student_can_submit_own_assignment(self):
        assignment = Assignment.objects.create(
            section=self.section, subject=self.subject, title="HW1",
            due_date=date.today() + timedelta(days=1), session=self.session,
        )
        submission = AssignmentSubmission.objects.create(assignment=assignment, student=self.student_user)
        self.client.force_authenticate(user=self.student_user)
        response = self.client.patch(
            reverse("assignment-submission-detail", args=[submission.id]), {"grade": "A"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        submission.refresh_from_db()
        self.assertEqual(submission.status, AssignmentSubmission.Status.SUBMITTED)
        self.assertIsNotNone(submission.submitted_at)

    def test_teacher_can_grade_submission(self):
        assignment = Assignment.objects.create(
            section=self.section, subject=self.subject, title="HW1",
            due_date=date.today() + timedelta(days=1), session=self.session,
        )
        submission = AssignmentSubmission.objects.create(
            assignment=assignment, student=self.student_user,
            status=AssignmentSubmission.Status.SUBMITTED, submitted_at=timezone.now(),
        )
        self.client.force_authenticate(user=self.teacher_user)
        response = self.client.patch(
            reverse("assignment-submission-detail", args=[submission.id]), {"grade": "A", "feedback": "Great work"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        submission.refresh_from_db()
        self.assertEqual(submission.grade, "A")

    def test_unrelated_user_cannot_grade_submission(self):
        assignment = Assignment.objects.create(
            section=self.section, subject=self.subject, title="HW1",
            due_date=date.today() + timedelta(days=1), session=self.session,
        )
        submission = AssignmentSubmission.objects.create(assignment=assignment, student=self.student_user)
        self.client.force_authenticate(user=self.outsider)
        response = self.client.patch(
            reverse("assignment-submission-detail", args=[submission.id]), {"grade": "A"}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class SyllabusProgressTests(APITestCase):
    def setUp(self):
        make_full_fixture(self)

    def test_creating_unit_auto_creates_progress_row(self):
        self.client.force_authenticate(user=self.teacher_user)
        response = self.client.post(
            reverse("syllabus-unit-list"),
            {
                "subject": str(self.subject.id),
                "section": str(self.section.id),
                "session": str(self.session.id),
                "title": "Algebra basics",
                "order": 1,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        unit = SyllabusUnit.objects.get(id=response.data["id"])
        self.assertTrue(SyllabusProgress.objects.filter(syllabus_unit=unit).exists())

    def test_subject_teacher_can_mark_covered(self):
        unit = SyllabusUnit.objects.create(
            subject=self.subject, section=self.section, session=self.session, title="Algebra basics"
        )
        progress = SyllabusProgress.objects.create(syllabus_unit=unit)
        self.client.force_authenticate(user=self.teacher_user)
        response = self.client.post(reverse("syllabus-progress-mark-covered", args=[progress.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        progress.refresh_from_db()
        self.assertIsNotNone(progress.covered_on)
        self.assertEqual(progress.covered_by, self.teacher_staff)


class ResultEntryTests(APITestCase):
    def setUp(self):
        make_full_fixture(self)
        self.exam_term = ExamTerm.objects.create(
            session=self.session, name="Mid-Term", start_date=date(2026, 9, 1), end_date=date(2026, 9, 10)
        )

    def test_marks_obtained_cannot_exceed_max_marks(self):
        self.client.force_authenticate(user=self.teacher_user)
        response = self.client.post(
            reverse("result-entry-list"),
            {
                "enrollment": str(self.enrollment.id),
                "subject": str(self.subject.id),
                "exam_term": str(self.exam_term.id),
                "marks_obtained": "95",
                "max_marks": "90",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_publishing_result_notifies_student(self):
        self.client.force_authenticate(user=self.teacher_user)
        with mock.patch("campus.bridge.notify") as notify:
            response = self.client.post(
                reverse("result-entry-list"),
                {
                    "enrollment": str(self.enrollment.id),
                    "subject": str(self.subject.id),
                    "exam_term": str(self.exam_term.id),
                    "marks_obtained": "80",
                    "max_marks": "100",
                },
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        notify.assert_called_once()
        entry = ResultEntry.objects.get(id=response.data["id"])
        self.assertEqual(entry.entered_by, self.teacher_staff)

    def test_report_card_aggregates_entries(self):
        ResultEntry.objects.create(
            enrollment=self.enrollment, subject=self.subject, exam_term=self.exam_term,
            marks_obtained=Decimal("80"), max_marks=Decimal("100"), entered_by=self.teacher_staff,
        )
        self.client.force_authenticate(user=self.student_user)
        response = self.client.get(
            reverse("result-entry-report-card"),
            {"enrollment": str(self.enrollment.id), "exam_term": str(self.exam_term.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["percentage"], 80.0)


class FeeModuleTests(APITestCase):
    def setUp(self):
        make_full_fixture(self)
        self.campus.fee_module_enabled = True
        self.campus.save(update_fields=["fee_module_enabled"])

    def _make_invoice(self, amount="15000"):
        fee_structure = FeeStructure.objects.create(
            campus=self.campus, session=self.session, title="Term 1 Tuition",
            amount=Decimal(amount), due_date=date.today() + timedelta(days=30),
        )
        return FeeInvoice.objects.create(
            enrollment=self.enrollment, fee_structure=fee_structure, amount_due=Decimal(amount)
        )

    def test_fee_structure_rejected_when_module_disabled(self):
        self.campus.fee_module_enabled = False
        self.campus.save(update_fields=["fee_module_enabled"])
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            reverse("fee-structure-list"),
            {
                "campus": str(self.campus.id), "session": str(self.session.id),
                "title": "Term 1 Tuition", "amount": "15000.00", "due_date": str(date.today() + timedelta(days=30)),
            },
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_generate_invoices_is_idempotent(self):
        self.client.force_authenticate(user=self.admin)
        fee_structure = FeeStructure.objects.create(
            campus=self.campus, session=self.session, title="Term 1 Tuition",
            amount=Decimal("15000.00"), due_date=date.today() + timedelta(days=30),
        )
        url = reverse("fee-structure-generate-invoices", args=[fee_structure.id])
        first = self.client.post(url)
        second = self.client.post(url)
        self.assertEqual(first.data["invoices_created"], 1)
        self.assertEqual(second.data["invoices_created"], 0)
        self.assertEqual(FeeInvoice.objects.filter(fee_structure=fee_structure).count(), 1)

    def test_generate_invoices_rejected_when_module_disabled_after_structure_created(self):
        # FEE-1 gap: module was enabled when the FeeStructure was made,
        # then turned off before generate-invoices was called.
        self.client.force_authenticate(user=self.admin)
        fee_structure = FeeStructure.objects.create(
            campus=self.campus, session=self.session, title="Term 1 Tuition",
            amount=Decimal("15000.00"), due_date=date.today() + timedelta(days=30),
        )
        self.campus.fee_module_enabled = False
        self.campus.save(update_fields=["fee_module_enabled"])
        response = self.client.post(reverse("fee-structure-generate-invoices", args=[fee_structure.id]))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_pay_debits_wallet_exact_balance_and_marks_invoice_paid(self):
        invoice = self._make_invoice("15000")
        self.student_user.coin = 15000
        self.student_user.save(update_fields=["coin"])
        self.client.force_authenticate(user=self.student_user)

        response = self.client.post(reverse("fee-payment-pay"), {"invoice": str(invoice.id)})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        payment = FeePayment.objects.get(id=response.data["id"])
        self.assertEqual(payment.status, FeePayment.Status.SUCCESS)
        self.assertEqual(payment.payment_mode, FeePayment.Mode.WALLET)

        self.student_user.refresh_from_db(fields=["coin"])
        self.assertEqual(self.student_user.coin, 0)

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, FeeInvoice.Status.PAID)

        ledger_entry = CoinLedger.objects.get(reference=f"fee-payment-{payment.id}")
        self.assertEqual(ledger_entry.amount, -15000)
        self.assertEqual(ledger_entry.transaction_type, CoinLedger.TransactionType.SPEND)

    def test_pay_with_insufficient_balance_returns_402_and_does_not_debit(self):
        invoice = self._make_invoice("15000")
        self.student_user.coin = 5000
        self.student_user.save(update_fields=["coin"])
        self.client.force_authenticate(user=self.student_user)

        response = self.client.post(reverse("fee-payment-pay"), {"invoice": str(invoice.id)})
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(response.data["current_balance"], 5000)
        self.assertEqual(response.data["coins_needed"], 10000)

        self.student_user.refresh_from_db(fields=["coin"])
        self.assertEqual(self.student_user.coin, 5000)  # untouched

        payment = FeePayment.objects.get(invoice=invoice)
        self.assertEqual(payment.status, FeePayment.Status.FAILED)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, FeeInvoice.Status.PENDING)

    def test_pay_is_idempotent_on_repeated_gateway_reference(self):
        invoice = self._make_invoice("15000")
        self.student_user.coin = 15000
        self.student_user.save(update_fields=["coin"])
        self.client.force_authenticate(user=self.student_user)

        payload = {"invoice": str(invoice.id), "gateway_reference": "retry-key-1"}
        first = self.client.post(reverse("fee-payment-pay"), payload)
        second = self.client.post(reverse("fee-payment-pay"), payload)

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["id"], second.data["id"])

        # Exactly one FeePayment and one CoinLedger row — the retried
        # request must not have created a second of either.
        self.assertEqual(FeePayment.objects.filter(invoice=invoice).count(), 1)
        self.assertEqual(CoinLedger.objects.filter(user=self.student_user).count(), 1)

        self.student_user.refresh_from_db(fields=["coin"])
        self.assertEqual(self.student_user.coin, 0)  # debited exactly once, not twice

    def test_pay_rejects_fractional_amount(self):
        invoice = self._make_invoice("15000")
        self.student_user.coin = 15000
        self.student_user.save(update_fields=["coin"])
        self.client.force_authenticate(user=self.student_user)

        response = self.client.post(
            reverse("fee-payment-pay"), {"invoice": str(invoice.id), "amount": "150.50"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(FeePayment.objects.filter(invoice=invoice).count(), 0)

    def test_pay_rejected_when_module_disabled(self):
        invoice = self._make_invoice("15000")
        self.campus.fee_module_enabled = False
        self.campus.save(update_fields=["fee_module_enabled"])
        self.student_user.coin = 15000
        self.student_user.save(update_fields=["coin"])
        self.client.force_authenticate(user=self.student_user)

        response = self.client.post(reverse("fee-payment-pay"), {"invoice": str(invoice.id)})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_office_can_record_cash_payment(self):
        invoice = self._make_invoice("15000")
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            reverse("fee-payment-record"),
            {"invoice": str(invoice.id), "amount": "15000.00", "payment_mode": "cash"},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, FeeInvoice.Status.PAID)

    def test_record_rejects_wallet_mode(self):
        invoice = self._make_invoice("15000")
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            reverse("fee-payment-record"),
            {"invoice": str(invoice.id), "amount": "15000.00", "payment_mode": "wallet"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_refund_credits_wallet_back_and_unmarks_invoice_paid(self):
        invoice = self._make_invoice("15000")
        self.student_user.coin = 15000
        self.student_user.save(update_fields=["coin"])
        self.client.force_authenticate(user=self.student_user)
        pay_response = self.client.post(reverse("fee-payment-pay"), {"invoice": str(invoice.id)})
        payment = FeePayment.objects.get(id=pay_response.data["id"])

        self.client.force_authenticate(user=self.admin)
        refund_response = self.client.post(reverse("fee-payment-refund", args=[payment.id]))
        self.assertEqual(refund_response.status_code, status.HTTP_200_OK)

        payment.refresh_from_db()
        self.assertEqual(payment.status, FeePayment.Status.REFUNDED)

        self.student_user.refresh_from_db(fields=["coin"])
        self.assertEqual(self.student_user.coin, 15000)  # credited back

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, FeeInvoice.Status.PENDING)  # no longer counted as paid

        refund_ledger_entry = CoinLedger.objects.get(reference=f"fee-refund-{payment.id}")
        self.assertEqual(refund_ledger_entry.amount, 15000)
        self.assertEqual(refund_ledger_entry.transaction_type, CoinLedger.TransactionType.REFUND)

    def test_refund_rejects_non_success_payment(self):
        invoice = self._make_invoice("15000")
        cash_payment = FeePayment.objects.create(
            invoice=invoice, amount=Decimal("15000"), payer_role=FeePayment.PayerRole.ADMIN,
            payment_mode=FeePayment.Mode.CASH, status=FeePayment.Status.SUCCESS, recorded_by=self.admin,
        )
        self.client.force_authenticate(user=self.admin)
        # Cash payments were never debited from a wallet — nothing to refund through it.
        response = self.client.post(reverse("fee-payment-refund", args=[cash_payment.id]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class DigitalIDCardTests(APITestCase):
    def setUp(self):
        make_full_fixture(self)

    def test_student_can_issue_own_card(self):
        self.client.force_authenticate(user=self.student_user)
        response = self.client.post(
            reverse("digital-id-card-list"),
            {"user": str(self.student_user.id), "campus": str(self.campus.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        card = DigitalIDCard.objects.get(id=response.data["id"])
        self.assertTrue(card.qr_token)

    def test_student_cannot_issue_card_for_someone_else(self):
        self.client.force_authenticate(user=self.student_user)
        response = self.client.post(
            reverse("digital-id-card-list"),
            {"user": str(self.outsider.id), "campus": str(self.campus.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_issue_card_for_a_student(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            reverse("digital-id-card-list"),
            {"user": str(self.student_user.id), "campus": str(self.campus.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class ParentLinkVerifyTests(APITestCase):
    def setUp(self):
        make_full_fixture(self)
        self.parent = User.objects.create_user(username="parent1", password="pass12345")

    def test_valid_token_creates_parent_link(self):
        self.client.force_authenticate(user=self.parent)
        with mock.patch("campus.bridge.resolve_parent_from_token", return_value=(self.parent, self.student_user)):
            response = self.client.post(
                reverse("campus-parent-link-verify"),
                {"campus": str(self.campus.id), "token": "some-valid-token"},
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            self.campus.parent_links.filter(parent=self.parent, student=self.student_user).exists()
        )

    def test_invalid_token_is_rejected(self):
        self.client.force_authenticate(user=self.parent)
        with mock.patch("campus.bridge.resolve_parent_from_token", return_value=(None, None)):
            response = self.client.post(
                reverse("campus-parent-link-verify"),
                {"campus": str(self.campus.id), "token": "bad-token"},
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class AcademicSessionRolloverRegressionTests(APITestCase):
    """
    Regression coverage for the `AcademicSession.save()` auto-flip fix —
    without it, creating a second `is_current=True` row for the same
    campus raises `IntegrityError` against `one_current_session_per_campus`
    instead of the previous row quietly flipping to `False`.
    """

    def test_plain_orm_create_does_not_violate_unique_constraint(self):
        admin = User.objects.create_user(username="admin2", password="pass12345")
        campus = Campus.objects.create(name="Rollover Test", created_by=admin)
        s1 = make_session(campus, name="2025-26", is_current=True)
        s2 = make_session(campus, name="2026-27", is_current=True)  # must not raise IntegrityError
        s1.refresh_from_db()
        self.assertFalse(s1.is_current)
        self.assertTrue(AcademicSession.objects.get(pk=s2.pk).is_current)