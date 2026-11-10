# campus/tests_admission.py
"""
Admission: enquiry -> application -> interview -> offer -> enrolled, duplicates, seats,
admission-fee invoices, CSV import / export, summary, visibility, push.

Run: `python manage.py test campus.tests_admission`

Celery / FCM are never touched: `dispatch_after_commit` is mocked on the API paths and
`campus.bridge` on the push paths.
"""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from . import admission as svc
from . import admission_push
from . import tasks as campus_tasks
from .bridge import NotifTypes
from .models import (
    AdmissionApplication,
    CampusAuditLog,
    FeeInvoice,
    FeeStructure,
    StudentEnrollment,
)
from .tests_t4 import T4Base

User = get_user_model()
A = AdmissionApplication.Status


def results(resp):
    data = resp.data
    return data["results"] if isinstance(data, dict) and "results" in data else data


class AdmissionBase(T4Base):
    def setUp(self):
        super().setUp()
        self.newbie = User.objects.create_user(username="newbie", password="pass12345")

    def body(self, **over):
        data = {
            "campus": str(self.campus.id),
            "applicant_name": "Asha Verma",
            "guardian_name": "Ravi Verma",
            "guardian_phone": "98765 43210",
            "school_class": str(self.cls.id),
        }
        data.update(over)
        return data

    def create(self, user=None, **over):
        self.as_(user or self.admin)
        return self.client.post(reverse("campus-admission-list"), self.body(**over), format="json")

    def make(self, **over):
        resp = self.create(**over)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        return AdmissionApplication.objects.get(pk=resp.data["id"])

    def move(self, app, to, user=None, **extra):
        self.as_(user or self.admin)
        return self.client.post(
            reverse("campus-admission-move", kwargs={"pk": app.pk}), {"to": to, **extra}, format="json"
        )

    def offer(self, app):
        self.assertEqual(self.move(app, "applied").status_code, 200)
        resp = self.move(app, "offered")
        self.assertEqual(resp.status_code, 200, resp.data)
        app.refresh_from_db()
        return app

    def do_enroll(self, app, user=None, **over):
        self.as_(user or self.admin)
        body = {"student": "newbie", "section": str(self.secB.id)}
        body.update(over)
        with mock.patch("campus.admission_views.dispatch_after_commit") as dispatch:
            resp = self.client.post(reverse("campus-admission-enroll", kwargs={"pk": app.pk}), body, format="json")
        return resp, dispatch


class CreateTests(AdmissionBase):
    def test_admin_creates_enquiry_with_gap_free_number(self):
        a = self.make()
        b = self.make(applicant_name="Bhavya Rao", guardian_phone="9000000001")
        self.assertEqual(a.status, A.ENQUIRY)
        self.assertEqual(a.session_id, self.session.id)  # defaults to the current session
        self.assertEqual(a.application_no, f"ADM/{self.session.name}/0001")
        self.assertEqual(b.application_no, f"ADM/{self.session.name}/0002")
        self.assertEqual(a.guardian_phone, "9876543210")  # normalised
        self.assertEqual(a.created_by_id, self.admin.id)
        self.assertTrue(CampusAuditLog.objects.filter(action="admission.create", campus=self.campus).exists())

    def test_principal_can_create_teachers_students_cannot(self):
        self.assertEqual(self.create(user=self.hod).status_code, status.HTTP_201_CREATED)
        for user in (self.ct, self.sub, self.mod, self.students[0], self.parent):
            self.assertEqual(self.create(user=user, applicant_name="X Y").status_code, status.HTTP_403_FORBIDDEN, user)

    def test_duplicate_refused_unless_forced(self):
        self.make()
        resp = self.create()
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp.data.get("code"), "duplicate_application")
        self.assertEqual(self.create(force=True).status_code, status.HTTP_201_CREATED)

    def test_rejected_duplicate_can_apply_again(self):
        app = self.make()
        self.assertEqual(self.move(app, "rejected", note="not eligible").status_code, 200)
        self.assertEqual(self.create().status_code, status.HTTP_201_CREATED)

    def test_class_must_belong_to_campus_session(self):
        resp = self.create(school_class="00000000-0000-0000-0000-000000000000")
        self.assertEqual(resp.status_code, 404)

    def test_bad_phone_and_future_dob_rejected(self):
        self.assertEqual(self.create(guardian_phone="12").status_code, 400)
        future = (timezone.localdate() + timedelta(days=5)).isoformat()
        self.assertEqual(self.create(date_of_birth=future, guardian_phone="9000000002").status_code, 400)

    def test_no_current_session_blocks_create(self):
        type(self.session).objects.filter(pk=self.session.pk).update(is_current=False)
        resp = self.create()
        self.assertEqual(resp.status_code, 400)


class FlowTests(AdmissionBase):
    def test_happy_path_enquiry_to_enrolled_with_invoice(self):
        FeeStructure.objects.create(
            campus=self.campus, session=self.session, school_class=self.cls, title="Admission fee",
            amount=Decimal("5000"), due_date=timezone.localdate() + timedelta(days=10),
            category=FeeStructure.Category.ADMISSION,
        )
        FeeStructure.objects.create(  # not an admission fee -> no invoice at enrolment
            campus=self.campus, session=self.session, school_class=self.cls, title="Tuition",
            amount=Decimal("9000"), due_date=timezone.localdate() + timedelta(days=10),
            category=FeeStructure.Category.TUITION,
        )
        app = self.make()
        self.assertEqual(self.move(app, "applied").status_code, 200)
        resp = self.move(app, "interview", interview_on=(timezone.now() + timedelta(days=2)).isoformat())
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "interview")
        self.assertIsNotNone(resp.data["interview_on"])
        self.assertEqual(self.move(app, "offered").status_code, 200)

        resp, dispatch = self.do_enroll(app)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "enrolled")
        self.assertEqual(resp.data["invoices_created"], 1)
        app.refresh_from_db()
        self.assertEqual(app.student_id, self.newbie.id)
        enrollment = StudentEnrollment.objects.get(student=self.newbie)
        self.assertEqual(enrollment.section_id, self.secB.id)
        self.assertEqual(app.enrollment_id, enrollment.id)
        self.assertEqual(FeeInvoice.objects.filter(enrollment=enrollment).count(), 1)
        dispatch.assert_called_once_with(campus_tasks.send_admission_notifications, str(app.pk), "enrolled")

    def test_cannot_jump_to_offered_from_enquiry_or_to_enrolled_via_move(self):
        app = self.make()
        resp = self.move(app, "offered")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data.get("code"), "bad_transition")
        self.offer(app)
        resp = self.move(app, "enrolled")
        self.assertEqual(resp.status_code, 400)

    def test_reject_needs_a_note_and_reopen_clears_decision(self):
        app = self.make()
        self.assertEqual(self.move(app, "rejected").status_code, 400)
        self.assertEqual(self.move(app, "rejected", note="age limit").status_code, 200)
        app.refresh_from_db()
        self.assertEqual(app.decision_note, "age limit")
        self.assertEqual(self.move(app, "applied").status_code, 200)  # reopen
        app.refresh_from_db()
        self.assertEqual((app.decision_note, app.decided_by_id), ("", None))

    def test_move_is_idempotent(self):
        app = self.make()
        self.move(app, "applied")
        self.assertEqual(self.move(app, "applied").status_code, 200)

    def test_enroll_requires_offer_and_real_student_and_section(self):
        app = self.make()
        resp, _ = self.do_enroll(app)
        self.assertEqual(resp.status_code, 409)  # not offered yet
        self.offer(app)
        resp, _ = self.do_enroll(app, student="nobody")
        self.assertEqual(resp.status_code, 404)
        resp, _ = self.do_enroll(app, section=None)
        self.assertEqual(resp.status_code, 400)  # no section allotted
        resp, _ = self.do_enroll(app)
        self.assertEqual(resp.status_code, 200, resp.data)
        resp, _ = self.do_enroll(app)
        self.assertEqual(resp.status_code, 409)  # already enrolled

    def test_enroll_section_must_be_in_offered_class(self):
        app = self.offer(self.make())
        resp, _ = self.do_enroll(app, section=str(self.secC.id))  # a class-11 section
        self.assertEqual(resp.status_code, 404)

    def test_student_already_active_in_campus_refused(self):
        StudentEnrollment.objects.create(student=self.newbie, section=self.secA, session=self.session)
        app = self.offer(self.make())
        resp, _ = self.do_enroll(app)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data.get("code"), "already_student")

    def test_section_capacity_enforced_on_enroll(self):
        StudentEnrollment.objects.create(student=self.students[0], section=self.secA, session=self.session)
        StudentEnrollment.objects.create(student=self.students[1], section=self.secA, session=self.session)
        app = self.offer(self.make())
        resp, _ = self.do_enroll(app, section=str(self.secA.id))  # capacity 2, already full
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data.get("code"), "section_full")

    def test_edit_after_enrolled_only_touches_remarks_and_follow_up(self):
        app = self.offer(self.make())
        self.do_enroll(app)
        self.as_(self.admin)
        resp = self.client.post(
            reverse("campus-admission-edit", kwargs={"pk": app.pk}),
            {"applicant_name": "Changed", "remarks": "fee plan agreed"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        app.refresh_from_db()
        self.assertEqual((app.applicant_name, app.remarks), ("Asha Verma", "fee plan agreed"))


class SeatTests(AdmissionBase):
    def setUp(self):
        super().setUp()
        self.secC.capacity = 1
        self.secC.save()

    def test_offer_blocked_when_class_has_no_free_seat(self):
        first = self.make(school_class=str(self.cls2.id))
        self.offer(first)  # takes the only seat
        second = self.make(applicant_name="Second Child", guardian_phone="9000000003", school_class=str(self.cls2.id))
        self.move(second, "applied")
        resp = self.move(second, "offered")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data.get("code"), "no_seats")
        self.assertEqual(self.move(second, "waitlisted").status_code, 200)  # waitlist instead

    def test_seat_frees_when_offer_withdrawn(self):
        first = self.offer(self.make(school_class=str(self.cls2.id)))
        second = self.make(applicant_name="Second Child", guardian_phone="9000000003", school_class=str(self.cls2.id))
        self.move(second, "applied")
        self.assertEqual(self.move(first, "withdrawn", note="took another school").status_code, 200)
        self.assertEqual(self.move(second, "offered").status_code, 200)

    def test_class_with_unlimited_section_never_runs_out(self):
        for i in range(4):
            app = self.make(applicant_name=f"Kid {i}", guardian_phone=f"90000001{i:02d}")
            self.offer(app)  # class 10 has an unlimited section (B)

    def test_seats_info_counts_enrolled_and_offers(self):
        StudentEnrollment.objects.create(student=self.students[0], section=self.secC, session=self.session)
        info = svc.seats_info(self.cls2, self.session)
        self.assertEqual((info["capacity"], info["enrolled"], info["offered"], info["seats_left"]), (1, 1, 0, 0))

    def test_offer_needs_a_class(self):
        app = self.make(school_class=None)
        self.move(app, "applied")
        resp = self.move(app, "offered")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("code"), "class_required")


class VisibilityTests(AdmissionBase):
    def test_only_campus_admins_see_admissions(self):
        app = self.make()
        for user in (self.ct, self.sub, self.mod, self.students[0], self.parent):
            self.as_(user)
            self.assertEqual(results(self.client.get(reverse("campus-admission-list"))), [], user)
            resp = self.client.get(reverse("campus-admission-detail", kwargs={"pk": app.pk}))
            self.assertEqual(resp.status_code, 404, user)
            resp = self.client.post(reverse("campus-admission-move", kwargs={"pk": app.pk}), {"to": "applied"}, format="json")
            self.assertEqual(resp.status_code, 404, user)

    def test_admin_of_another_campus_sees_nothing(self):
        from .models import Campus, StaffProfile

        other = User.objects.create_user(username="otheradmin", password="pass12345")
        c2 = Campus.objects.create(name="C2", created_by=other, verification_status=Campus.VerificationStatus.APPROVED)
        StaffProfile.objects.create(campus=c2, user=other, role=StaffProfile.Role.ADMIN)
        app = self.make()
        self.as_(other)
        self.assertEqual(results(self.client.get(reverse("campus-admission-list"))), [])
        self.assertEqual(self.client.get(reverse("campus-admission-detail", kwargs={"pk": app.pk})).status_code, 404)
        resp = self.client.post(reverse("campus-admission-list"), self.body(), format="json")
        self.assertEqual(resp.status_code, 403)

    def test_filters_and_search(self):
        a = self.make()
        b = self.make(applicant_name="Bhavya Rao", guardian_phone="9111122222")
        self.move(b, "applied")
        self.as_(self.admin)
        url = reverse("campus-admission-list")
        self.assertEqual(len(results(self.client.get(url))), 2)
        self.assertEqual([r["id"] for r in results(self.client.get(url, {"status": "applied"}))], [str(b.id)])
        self.assertEqual([r["id"] for r in results(self.client.get(url, {"q": "98765"}))], [str(a.id)])
        self.assertEqual([r["id"] for r in results(self.client.get(url, {"q": "ADM/"}))].__len__(), 2)
        self.assertEqual(self.client.get(url, {"status": "bogus"}).status_code, 400)

    def test_follow_up_filter_and_flag(self):
        today = timezone.localdate()
        a = self.make(follow_up_on=(today - timedelta(days=1)).isoformat())
        self.make(applicant_name="Later Kid", guardian_phone="9222233333", follow_up_on=(today + timedelta(days=3)).isoformat())
        self.as_(self.admin)
        rows = results(self.client.get(reverse("campus-admission-list"), {"follow_up": "due"}))
        self.assertEqual([r["id"] for r in rows], [str(a.id)])
        self.assertTrue(rows[0]["follow_up_due"])


class SummaryExportImportTests(AdmissionBase):
    def test_summary_funnel_and_conversion(self):
        a = self.offer(self.make())
        self.do_enroll(a)
        self.make(applicant_name="Two", guardian_phone="9333344444", follow_up_on=timezone.localdate().isoformat())
        c = self.make(applicant_name="Three", guardian_phone="9444455555")
        self.move(c, "rejected", note="no seat in class")
        self.as_(self.admin)
        data = self.client.get(reverse("campus-admission-summary"), {"campus": str(self.campus.id)}).data
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["by_status"]["enrolled"], 1)
        self.assertEqual(data["by_status"]["enquiry"], 1)
        self.assertEqual(data["by_status"]["rejected"], 1)
        self.assertEqual(data["conversion_percent"], 33.3)
        self.assertEqual(data["follow_up_due"], 1)
        self.assertEqual(data["open"], 1)

    def test_summary_forbidden_for_teacher(self):
        self.as_(self.ct)
        resp = self.client.get(reverse("campus-admission-summary"), {"campus": str(self.campus.id)})
        self.assertEqual(resp.status_code, 403)

    def test_export_csv_escapes_formulas(self):
        self.make(applicant_name="=HYPERLINK(1)", guardian_phone="9555566666")
        self.as_(self.admin)
        resp = self.client.get(reverse("campus-admission-export"), {"campus": str(self.campus.id)})
        self.assertEqual(resp.status_code, 200)
        text = resp.content.decode()
        self.assertIn("application_no,applicant_name", text)
        self.assertIn("'=HYPERLINK(1)", text)

    CSV = (
        "Name,Parent,Phone,Class,DOB\n"
        "Kid One,Mom One,98000 00001,10,12/03/2019\n"
        "Kid Two,Mom Two,98000 00002,Nope,2018-05-06\n"
        ",Mom Three,98000 00003,10,\n"
        "Kid One,Mom One,98000 00001,10,\n"
    )

    def post_import(self, **over):
        self.as_(self.admin)
        body = {"campus": str(self.campus.id), "csv": self.CSV}
        body.update(over)
        return self.client.post(reverse("campus-admission-import-csv"), body, format="json")

    def test_import_preview_saves_nothing(self):
        resp = self.post_import()
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual((resp.data["committed"], resp.data["ready"], resp.data["created"]), (False, 2, 0))
        self.assertEqual(len(resp.data["skipped"]), 2)  # empty name + repeated row
        self.assertEqual(resp.data["class_not_matched"], [3])
        self.assertEqual(AdmissionApplication.objects.count(), 0)

    def test_import_commit_creates_enquiries_and_is_not_repeatable(self):
        resp = self.post_import(commit=True)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["created"], 2)
        rows = AdmissionApplication.objects.order_by("application_no")
        self.assertEqual([r.source for r in rows], ["import", "import"])
        self.assertEqual(rows[0].school_class_id, self.cls.id)
        self.assertIsNone(rows[1].school_class_id)  # unknown class name left blank
        again = self.post_import(commit=True)
        self.assertEqual(again.data["created"], 0)  # "Already on file"
        self.assertEqual(AdmissionApplication.objects.count(), 2)

    def test_import_needs_name_column_and_admin(self):
        resp = self.post_import(csv="foo,bar\n1,2\n")
        self.assertEqual(resp.status_code, 400)
        self.as_(self.ct)
        resp = self.client.post(
            reverse("campus-admission-import-csv"), {"campus": str(self.campus.id), "csv": self.CSV}, format="json"
        )
        self.assertEqual(resp.status_code, 403)


class PushTests(AdmissionBase):
    def test_enrolled_task_pushes_to_student_only(self):
        app = self.offer(self.make(guardian_phone="9876543210", remarks="secret note"))
        self.do_enroll(app)
        with mock.patch("campus.admission_push.bridge.notify") as notify:
            out = campus_tasks.send_admission_notifications(str(app.pk), "enrolled")
        self.assertEqual(out, {"sent": 1})
        kwargs = notify.call_args.kwargs
        self.assertEqual([u.pk for u in kwargs["users"]], [self.newbie.pk])
        self.assertEqual(kwargs["notif_type"], NotifTypes.CAMPUS_ADMISSION)
        self.assertTrue(kwargs["push"])
        blob = f"{kwargs['title']} {kwargs['body']}"
        self.assertNotIn("secret note", blob)
        self.assertNotIn("9876543210", blob)

    def test_enrolled_task_skips_when_not_enrolled(self):
        app = self.make()
        with mock.patch("campus.admission_push.bridge.notify") as notify:
            out = campus_tasks.send_admission_notifications(str(app.pk), "enrolled")
        self.assertEqual(out, {"sent": 0})
        notify.assert_not_called()

    def test_followup_digest_goes_to_admins_once_per_campus(self):
        today = timezone.localdate()
        self.make(follow_up_on=today.isoformat())
        self.make(applicant_name="Late Kid", guardian_phone="9666677777", follow_up_on=(today - timedelta(days=2)).isoformat())
        self.make(applicant_name="Future Kid", guardian_phone="9777788888", follow_up_on=(today + timedelta(days=2)).isoformat())
        with mock.patch("campus.admission_push.bridge.notify") as notify:
            sent = admission_push.notify_followup_digest()
        self.assertEqual(sent, 1)
        kwargs = notify.call_args.kwargs
        self.assertEqual({u.pk for u in kwargs["users"]}, {self.admin.pk, self.hod.pk})
        self.assertEqual(kwargs["data"]["count"], 2)
        self.assertNotIn("Verma", kwargs["body"])

    def test_followup_digest_silent_when_nothing_due(self):
        self.make()
        with mock.patch("campus.admission_push.bridge.notify") as notify:
            self.assertEqual(admission_push.notify_followup_digest(), 0)
        notify.assert_not_called()


class PureHelperTests(AdmissionBase):
    def test_phone_normalisation(self):
        self.assertEqual(svc.normalize_phone("98765-43210"), "9876543210")
        self.assertEqual(svc.normalize_phone("+91 98765 43210"), "+919876543210")
        self.assertEqual(svc.normalize_phone(""), "")
        with self.assertRaises(svc.AdmissionError):
            svc.normalize_phone("123")

    def test_every_status_has_transition_entry(self):
        self.assertEqual(set(svc.TRANSITIONS), set(A))
        self.assertEqual(svc.TRANSITIONS[A.ENROLLED], set())
        self.assertNotIn(A.ENROLLED, {t for moves in svc.TRANSITIONS.values() for t in moves})
