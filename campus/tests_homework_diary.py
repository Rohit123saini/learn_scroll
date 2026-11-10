# campus/tests_homework_diary.py
"""
Homework Diary (daily, class-wise): API scoping/permissions, validation,
attachment upload, soft delete, the `day` helper, and the two pushes.

Run: `python manage.py test campus.tests_homework_diary`

Celery / FCM are never touched: `dispatch_after_commit` is mocked on the API
paths and `campus.bridge.notify` / `push_to_parent_devices` on the push paths —
we assert WHO is pushed and WITH push=True, not delivery.
"""
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from . import tasks as campus_tasks
from .bridge import NotifTypes
from .models import (
    ClassTeacherassigments,
    HomeworkDiaryEntry,
    StaffProfile,
    SubjectTeacherassigments,
)
from .tests_t4_efg import T4Base, rows


class HomeworkBase(T4Base):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.today = timezone.localdate()
        self.tomorrow = self.today + timedelta(days=1)

    def make_entry(self, *, section=None, subject=None, poster=None, **kw):
        section = section or self.secA
        values = dict(
            campus=self.campus,
            session=self.session,
            section=section,
            subject=subject or self.math,
            posted_by=poster or self.teacher,
            title="Ex 4.2 Q1-5",
            diary_date=self.today,
            due_date=self.tomorrow,
        )
        values.update(kw)
        return HomeworkDiaryEntry.objects.create(**values)

    def body(self, **overrides):
        data = {
            "section": str(self.secA.id),
            "subject": str(self.math.id),
            "title": "Ex 4.2 Q1-5",
            "description": "Copy me karke aana",
            "due_date": self.tomorrow.isoformat(),
        }
        data.update(overrides)
        return data

    def post(self, user, **overrides):
        self.client.force_authenticate(user)
        with mock.patch("campus.homework_diary.dispatch_after_commit") as dispatch:
            resp = self.client.post(reverse("campus-homework-diary-list"), self.body(**overrides), format="json")
        return resp, dispatch


class CreateAndPermissionTests(HomeworkBase):
    def test_subject_teacher_posts_for_own_subject(self):
        resp, dispatch = self.post(self.teacher)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        entry = HomeworkDiaryEntry.objects.get(pk=resp.data["id"])
        self.assertEqual(entry.campus_id, self.campus.id)  # derived, not client-supplied
        self.assertEqual(entry.session_id, self.session.id)
        self.assertEqual(entry.posted_by_id, self.teacher.id)
        self.assertEqual(entry.diary_date, self.today)  # defaults to today
        self.assertEqual(resp.data["subject_name"], "Maths")
        self.assertEqual(resp.data["section_label"], "10 - A")
        # push fan-out is handed to a worker after commit, with just the id
        dispatch.assert_called_once_with(campus_tasks.send_homework_diary_posted, str(entry.pk))

    def test_cannot_post_for_unapproved_subject(self):
        resp, dispatch = self.post(self.teacher, subject=str(self.phy.id))  # PENDING assignment
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        dispatch.assert_not_called()

    def test_cannot_post_to_another_section(self):
        resp, _ = self.post(self.teacher, section=str(self.secB.id))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_class_teacher_posts_any_subject_of_own_section(self):
        ct_staff = StaffProfile.objects.create(
            campus=self.campus, user=self.other_teacher, role=StaffProfile.Role.CLASS_TEACHER
        )
        ClassTeacherassigments.objects.create(section=self.secA, staff=ct_staff)
        resp, _ = self.post(self.other_teacher, subject=str(self.phy.id))
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_admin_posts_anywhere(self):
        resp, _ = self.post(self.admin, section=str(self.secB.id))
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_student_and_parent_cannot_post(self):
        for user in (self.sa1, self.parent):
            resp, dispatch = self.post(user)
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, user.username)
            dispatch.assert_not_called()

    def test_garbage_section_id_is_not_a_500(self):
        resp, _ = self.post(self.teacher, section="not-a-uuid")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        resp = self.client.get(reverse("campus-homework-diary-list"))
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_throttle_scope_has_a_rate(self):
        # A scope with no rate = ImproperlyConfigured on the very first POST.
        self.assertIn("campus_homework_diary_post", settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])


class ValidationTests(HomeworkBase):
    def test_due_before_diary_date_rejected(self):
        resp, _ = self.post(self.teacher, diary_date=self.today.isoformat(),
                            due_date=(self.today - timedelta(days=1)).isoformat())
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("due_date", resp.data)

    def test_due_same_day_is_fine(self):
        resp, _ = self.post(self.teacher, due_date=self.today.isoformat())
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_diary_date_too_old_rejected(self):
        old = self.today - timedelta(days=60)
        resp, _ = self.post(self.teacher, diary_date=old.isoformat(), due_date=self.tomorrow.isoformat())
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("diary_date", resp.data)

    def test_due_date_absurdly_far_rejected(self):
        far = self.today + timedelta(days=900)
        resp, _ = self.post(self.teacher, due_date=far.isoformat())
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("due_date", resp.data)

    def test_blank_title_rejected(self):
        resp, _ = self.post(self.teacher, title="   ")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("title", resp.data)

    def test_subject_from_another_campus_rejected(self):
        from .models import Campus, Subject

        other = Campus.objects.create(name="Other", created_by=self.admin)
        foreign = Subject.objects.create(campus=other, name="Chemistry")
        resp, _ = self.post(self.admin, subject=str(foreign.id))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("subject", resp.data)


class AttachmentTests(HomeworkBase):
    def setUp(self):
        super().setUp()
        self._media = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._media)
        self._override.enable()

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self._media, ignore_errors=True)
        super().tearDown()

    def post_file(self, upload):
        self.client.force_authenticate(self.teacher)
        data = self.body()
        data["attachment"] = upload
        with mock.patch("campus.homework_diary.dispatch_after_commit"):
            return self.client.post(reverse("campus-homework-diary-list"), data, format="multipart")

    def test_pdf_attachment_uploads(self):
        resp = self.post_file(SimpleUploadedFile("worksheet.pdf", b"%PDF-1.4 test", content_type="application/pdf"))
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertTrue(resp.data["attachment"])
        self.assertEqual(resp.data["attachment_name"].rsplit(".", 1)[-1], "pdf")

    def test_disallowed_extension_rejected(self):
        resp = self.post_file(SimpleUploadedFile("run.exe", b"MZ", content_type="application/octet-stream"))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("attachment", resp.data)

    def test_remove_attachment_on_patch(self):
        resp = self.post_file(SimpleUploadedFile("w.pdf", b"%PDF-1.4 test", content_type="application/pdf"))
        pk = resp.data["id"]
        resp = self.client.patch(
            reverse("campus-homework-diary-detail", args=[pk]), {"remove_attachment": True}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertFalse(resp.data["attachment"])
        self.assertFalse(HomeworkDiaryEntry.objects.get(pk=pk).attachment)


class VisibilityTests(HomeworkBase):
    def setUp(self):
        super().setUp()
        self.in_a = self.make_entry(title="A homework")
        self.in_b = self.make_entry(section=self.secB, poster=self.other_teacher, title="B homework")

    def titles(self, user, **params):
        self.client.force_authenticate(user)
        resp = self.client.get(reverse("campus-homework-diary-list"), params)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        return {r["title"] for r in rows(resp)}

    def test_student_sees_only_own_section(self):
        self.assertEqual(self.titles(self.sa1), {"A homework"})
        self.assertEqual(self.titles(self.sb), {"B homework"})

    def test_other_sections_entry_is_404_for_student(self):
        self.client.force_authenticate(self.sa1)
        resp = self.client.get(reverse("campus-homework-diary-detail", args=[self.in_b.pk]))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_parent_sees_childs_section(self):
        self.assertEqual(self.titles(self.parent), {"A homework"})

    def test_parent_student_param_cannot_reach_other_children(self):
        # sb is NOT this parent's child -> empty, not a leak.
        self.assertEqual(self.titles(self.parent, student=str(self.sb.id)), set())
        self.assertEqual(self.titles(self.parent, student=str(self.sa1.id)), {"A homework"})

    def test_staff_sees_every_section_and_can_filter(self):
        self.assertEqual(self.titles(self.admin), {"A homework", "B homework"})
        self.assertEqual(self.titles(self.admin, section=str(self.secB.id)), {"B homework"})

    def test_date_and_upcoming_filters(self):
        self.make_entry(title="old", diary_date=self.today - timedelta(days=5),
                        due_date=self.today - timedelta(days=2))
        self.assertNotIn("old", self.titles(self.sa1, date=self.today.isoformat()))
        self.assertIn("old", self.titles(self.sa1, date=(self.today - timedelta(days=5)).isoformat()))
        self.assertNotIn("old", self.titles(self.sa1, upcoming="1"))  # due date already passed
        self.assertIn("A homework", self.titles(self.sa1, upcoming="1"))

    def test_bad_date_param_is_400(self):
        self.client.force_authenticate(self.sa1)
        resp = self.client.get(reverse("campus-homework-diary-list"), {"date": "31-12-2026"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_inactive_entries_hidden(self):
        self.in_a.is_active = False
        self.in_a.save()
        self.assertEqual(self.titles(self.sa1), set())


class EditDeleteTests(HomeworkBase):
    def setUp(self):
        super().setUp()
        self.entry = self.make_entry()
        self.url = reverse("campus-homework-diary-detail", args=[self.entry.pk])

    def test_owner_teacher_edits(self):
        self.client.force_authenticate(self.teacher)
        resp = self.client.patch(self.url, {"title": "Ex 4.3 Q1-3"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.title, "Ex 4.3 Q1-3")

    def test_other_teacher_cannot_edit(self):
        self.client.force_authenticate(self.other_teacher)  # teaches secB only
        resp = self.client.patch(self.url, {"title": "hijack"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_cannot_edit_or_delete(self):
        self.client.force_authenticate(self.sa1)
        self.assertEqual(self.client.patch(self.url, {"title": "x"}, format="json").status_code, 403)
        self.assertEqual(self.client.delete(self.url).status_code, 403)

    def test_section_cannot_be_changed(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.patch(self.url, {"section": str(self.secB.id)}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("section", resp.data)

    def test_cannot_switch_to_subject_one_does_not_teach(self):
        self.client.force_authenticate(self.teacher)
        resp = self.client.patch(self.url, {"subject": str(self.phy.id)}, format="json")  # pending only
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.subject_id, self.math.id)

    def test_new_due_date_rearms_the_reminder(self):
        self.entry.due_reminder_sent_at = timezone.now()
        self.entry.save()
        self.client.force_authenticate(self.teacher)
        later = (self.tomorrow + timedelta(days=3)).isoformat()
        resp = self.client.patch(self.url, {"due_date": later}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.entry.refresh_from_db()
        self.assertIsNone(self.entry.due_reminder_sent_at)

    def test_delete_is_soft(self):
        self.client.force_authenticate(self.teacher)
        self.assertEqual(self.client.delete(self.url).status_code, status.HTTP_204_NO_CONTENT)
        self.entry.refresh_from_db()
        self.assertFalse(self.entry.is_active)
        self.client.force_authenticate(self.sa1)
        self.assertEqual(rows(self.client.get(reverse("campus-homework-diary-list"))), [])


class DayEndpointTests(HomeworkBase):
    def setUp(self):
        super().setUp()
        # Physics gets an approved teacher too, so a section has 2 subjects to cover.
        SubjectTeacherassigments.objects.filter(section=self.secA, subject=self.phy).update(
            status=SubjectTeacherassigments.Status.APPROVED
        )
        self.url = reverse("campus-homework-diary-day")

    def get(self, user, **params):
        self.client.force_authenticate(user)
        params.setdefault("section", str(self.secA.id))
        return self.client.get(self.url, params)

    def test_admin_sees_every_missing_subject(self):
        self.make_entry()  # maths done
        resp = self.get(self.admin)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["date"], self.today.isoformat())
        self.assertEqual(len(resp.data["entries"]), 1)
        self.assertEqual([s["name"] for s in resp.data["missing_subjects"]], ["Physics"])

    def test_subject_teacher_sees_only_own_missing(self):
        # t_staff teaches BOTH subjects in secA (fixture), other_teacher neither.
        resp = self.get(self.teacher)
        self.assertEqual({s["name"] for s in resp.data["missing_subjects"]}, {"Maths", "Physics"})
        self.assertEqual(self.get(self.other_teacher).data["missing_subjects"], [])

    def test_student_gets_entries_but_no_missing_list(self):
        self.make_entry()
        resp = self.get(self.sa1)
        self.assertEqual(len(resp.data["entries"]), 1)
        self.assertEqual(resp.data["missing_subjects"], [])

    def test_student_cannot_peek_into_other_section(self):
        self.make_entry(section=self.secB, poster=self.other_teacher)
        resp = self.get(self.sa1, section=str(self.secB.id))
        self.assertEqual(resp.data["entries"], [])
        self.assertEqual(resp.data["missing_subjects"], [])

    def test_validation(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 400)  # section required
        self.assertEqual(self.get(self.admin, section="nope").status_code, 404)
        self.assertEqual(self.get(self.admin, date="garbage").status_code, 400)


class PushTests(HomeworkBase):
    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_posted_push_goes_to_students_and_parents_not_the_poster(self, notify, devices):
        entry = self.make_entry()
        campus_tasks.send_homework_diary_posted(str(entry.pk))
        notify.assert_called_once()
        kw = notify.call_args.kwargs
        self.assertEqual(kw["notif_type"], NotifTypes.HOMEWORK_DIARY_POSTED)
        self.assertTrue(kw["push"])
        self.assertEqual({u.pk for u in kw["users"]}, {self.sa1.pk, self.sa2.pk, self.parent.pk})
        self.assertNotIn(self.teacher.pk, {u.pk for u in kw["users"]})
        self.assertNotIn(self.sb.pk, {u.pk for u in kw["users"]})  # other section
        self.assertEqual(kw["data"]["section_id"], str(self.secA.id))
        self.assertEqual(kw["data"]["entry_id"], str(entry.pk))
        # loginless Parent Mode devices are tried once per student
        self.assertEqual(devices.call_count, 2)

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=1)
    @mock.patch("campus.bridge.notify")
    def test_loginless_parent_device_alone_is_enough(self, notify, devices):
        from .models import CampusParentLink

        CampusParentLink.objects.filter(parent=self.parent).delete()
        entry = self.make_entry()
        campus_tasks.send_homework_diary_posted(str(entry.pk))
        kw = notify.call_args.kwargs
        self.assertEqual({u.pk for u in kw["users"]}, {self.sa1.pk, self.sa2.pk})
        devices.assert_called()

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_deleted_entry_sends_nothing(self, notify, devices):
        entry = self.make_entry(is_active=False)
        self.assertEqual(campus_tasks.send_homework_diary_posted(str(entry.pk)), {"sent": 0})
        notify.assert_not_called()

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify", side_effect=RuntimeError("boom"))
    def test_push_failure_never_raises_out_of_the_task(self, notify, devices):
        entry = self.make_entry()
        self.assertEqual(campus_tasks.send_homework_diary_posted(str(entry.pk)), {"sent": 0})


class DueReminderTests(HomeworkBase):
    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_due_tomorrow_is_reminded_exactly_once(self, notify, devices):
        entry = self.make_entry(due_date=self.tomorrow)
        self.assertEqual(campus_tasks.send_homework_diary_due_reminders(), {"reminded": 1})
        kw = notify.call_args.kwargs
        self.assertEqual(kw["notif_type"], NotifTypes.HOMEWORK_DIARY_DUE)
        self.assertTrue(kw["push"])
        entry.refresh_from_db()
        self.assertIsNotNone(entry.due_reminder_sent_at)
        # second run (or an overlapping worker) must not double-send
        self.assertEqual(campus_tasks.send_homework_diary_due_reminders(), {"reminded": 0})
        self.assertEqual(notify.call_count, 1)

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_other_due_dates_and_deleted_entries_are_skipped(self, notify, devices):
        self.make_entry(due_date=self.today + timedelta(days=5))
        self.make_entry(due_date=self.tomorrow, is_active=False)
        self.assertEqual(campus_tasks.send_homework_diary_due_reminders(), {"reminded": 0})
        notify.assert_not_called()

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify", side_effect=RuntimeError("boom"))
    def test_failed_send_releases_the_claim_for_a_retry(self, notify, devices):
        entry = self.make_entry(due_date=self.tomorrow)
        # `notify` blowing up inside _send is the failure under test
        self.assertEqual(campus_tasks.send_homework_diary_due_reminders(), {"reminded": 0})
        entry.refresh_from_db()
        self.assertIsNone(entry.due_reminder_sent_at)
