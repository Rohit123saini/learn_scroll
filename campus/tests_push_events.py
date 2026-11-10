# campus/tests_push_events.py
"""
Push for: notice, absent (-> parent), fee due, live class.
Run: `python manage.py test campus.tests_push_events`.
bridge.notify / push_to_parent_devices are mocked: we assert WHO is pushed and
WITH push=True, not FCM delivery. on_commit callbacks are executed explicitly.
"""
from datetime import date
from unittest import mock

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from .attendance_push import notify_parents_of_absences
from .bridge import NotifTypes
from .models import Attendance, CampusParentLink, Notice
from .tests_t4_efg import T4Base


class PushBase(T4Base):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.today = timezone.localdate()
        self.client.force_authenticate(self.admin)

    def bulk(self, overrides, day=None, subject=None):
        body = {
            "section": str(self.secA.id),
            "date": (day or self.today).isoformat(),
            "default_status": "present",
            "overrides": overrides,
            # Changing an already-marked row is an edit and needs a reason
            # (campus/attendance_edit.py); harmless on a first marking.
            "reason": "Correction",
        }
        if subject:
            body["subject"] = str(subject.id)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("attendance-bulk"), body, format="json")


class AbsentParentPushTests(PushBase):
    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_bulk_absent_pushes_parent_only(self, notify, devices):
        resp = self.bulk({str(self.e_a1.id): "absent"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        notify.assert_called_once()
        kw = notify.call_args.kwargs
        self.assertEqual(kw["users"], [self.parent])
        self.assertEqual(kw["notif_type"], NotifTypes.CAMPUS_STUDENT_ABSENT)
        self.assertTrue(kw["push"])
        self.assertNotIn(self.sa1, kw["users"])

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_present_students_not_pushed(self, notify, devices):
        self.bulk({})
        notify.assert_not_called()

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_resave_unchanged_does_not_renotify(self, notify, devices):
        self.bulk({str(self.e_a1.id): "absent"})
        self.bulk({str(self.e_a1.id): "absent"})
        self.assertEqual(notify.call_count, 1)

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_one_push_per_child_per_day_across_subjects(self, notify, devices):
        self.bulk({str(self.e_a1.id): "absent"})
        self.bulk({str(self.e_a1.id): "absent"}, subject=self.math)
        self.assertEqual(notify.call_count, 1)

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_backdated_absence_not_pushed(self, notify, devices):
        old = self.today - timezone.timedelta(days=3)
        self.bulk({str(self.e_a1.id): "absent"}, day=old)
        notify.assert_not_called()

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_no_parent_means_no_push_and_marker_kept_free(self, notify, devices):
        self.bulk({str(self.e_a2.id): "absent"})  # sa2 has no parent link
        notify.assert_not_called()
        CampusParentLink.objects.create(campus=self.campus, parent=self.other_teacher, student=self.sa2)
        self.bulk({str(self.e_a2.id): "late"})
        self.bulk({str(self.e_a2.id): "absent"})
        notify.assert_called_once()

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=1)
    @mock.patch("campus.bridge.notify")
    def test_loginless_parent_device_is_pushed(self, notify, devices):
        self.bulk({str(self.e_a2.id): "absent"})  # no User parent, but a device
        notify.assert_not_called()
        devices.assert_called_once()

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_single_create_and_change_to_absent(self, notify, devices):
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(
                reverse("attendance-list"),
                {"enrollment": str(self.e_a1.id), "date": self.today.isoformat(), "status": "present"},
                format="json",
            )
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        notify.assert_not_called()
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.patch(
                reverse("attendance-detail", args=[r.data["id"]]), {"status": "absent", "reason": "Correction"}, format="json"
            )
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        notify.assert_called_once()

    @mock.patch("campus.bridge.notify", side_effect=RuntimeError("boom"))
    def test_push_failure_never_breaks_attendance_save(self, notify):
        resp = self.bulk({str(self.e_a1.id): "absent"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(Attendance.objects.filter(enrollment=self.e_a1, status="absent").exists())

    def test_helper_ignores_other_dates(self):
        self.assertEqual(
            notify_parents_of_absences(enrollment_ids=[self.e_a1.id], date=date(2020, 1, 1)), 0
        )


class AbsentCorrectionPushTests(PushBase):
    """Absent push already went out, then the teacher fixes the mark."""

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_absent_then_present_sends_correction(self, notify, devices):
        self.bulk({str(self.e_a1.id): "absent"})
        self.assertEqual(notify.call_count, 1)
        self.bulk({})  # everyone present now
        self.assertEqual(notify.call_count, 2)
        kw = notify.call_args.kwargs
        self.assertEqual(kw["users"], [self.parent])
        self.assertEqual(kw["notif_type"], NotifTypes.CAMPUS_STUDENT_ABSENT)
        self.assertEqual(kw["data"].get("corrected"), "1")
        self.assertTrue(kw["push"])

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_no_correction_when_parent_never_got_absent_push(self, notify, devices):
        self.bulk({str(self.e_a2.id): "absent"})  # sa2 has no parent -> nothing was sent
        self.bulk({})
        notify.assert_not_called()

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_no_correction_while_still_absent_in_another_register(self, notify, devices):
        self.bulk({str(self.e_a1.id): "absent"})  # whole day: parent told
        self.bulk({str(self.e_a1.id): "absent"}, subject=self.math)  # same day, no 2nd push
        self.bulk({})  # whole-day row corrected, the Maths row is still absent
        self.assertEqual(notify.call_count, 1)

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_absent_again_after_correction_notifies_again(self, notify, devices):
        self.bulk({str(self.e_a1.id): "absent"})
        self.bulk({})
        self.bulk({str(self.e_a1.id): "absent"})
        self.assertEqual(notify.call_count, 3)

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_single_patch_absent_to_present_sends_correction(self, notify, devices):
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(
                reverse("attendance-list"),
                {"enrollment": str(self.e_a1.id), "date": self.today.isoformat(), "status": "absent"},
                format="json",
            )
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        self.assertEqual(notify.call_count, 1)
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.patch(
                reverse("attendance-detail", args=[r.data["id"]]),
                {"status": "present", "reason": "Marked by mistake"},
                format="json",
            )
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual(notify.call_count, 2)
        self.assertEqual(notify.call_args.kwargs["data"].get("corrected"), "1")

    def test_helper_ignores_other_dates(self):
        from .attendance_push import notify_parents_of_corrections

        self.assertEqual(notify_parents_of_corrections(enrollment_ids=[self.e_a1.id], date=date(2020, 1, 1)), 0)


class NoticeLiveFeePushTests(PushBase):
    @mock.patch("campus.bridge.notify")
    def test_notice_pushes_students_and_parents(self, notify):
        r = self.client.post(
            reverse("notice-list"),
            {
                "campus": str(self.campus.id), "session": str(self.session.id),
                "section": str(self.secA.id), "title": "Holiday", "body": "School closed Monday",
            },
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        self.assertTrue(Notice.objects.filter(title="Holiday").exists())
        kw = notify.call_args.kwargs
        self.assertTrue(kw["push"])
        self.assertEqual(kw["notif_type"], NotifTypes.NOTICE_POSTED)
        ids = {getattr(u, "pk", u) for u in kw["users"]}
        self.assertEqual(ids, {self.sa1.pk, self.sa2.pk, self.parent.pk})

    @mock.patch("campus.tasks.bridge.notify")
    def test_fee_reminder_is_pushed(self, notify):
        from .models import FeeInvoice, FeeStructure
        from . import tasks

        fs = FeeStructure.objects.create(
            campus=self.campus, session=self.session, title="Term 1", amount=500,
            due_date=timezone.localdate(),
        ) if hasattr(FeeStructure, "title") else None
        if fs is None:
            self.skipTest("FeeStructure shape differs")
        FeeInvoice.objects.create(enrollment=self.e_a1, fee_structure=fs, amount_due=500)
        tasks.send_fee_due_reminders()
        self.assertTrue(notify.call_args.kwargs["push"])
