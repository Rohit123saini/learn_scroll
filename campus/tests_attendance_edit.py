# campus/tests_attendance_edit.py
"""Attendance galti-sudhaar: edit window + reason + audit log (campus/attendance_edit.py)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from .models import Attendance, AttendanceEditLog, CampusAuditLog
from .tests_teacher_flows import TeacherFlowBase

User = get_user_model()


class AttendanceEditBase(TeacherFlowBase):
    def _bulk(self, overrides=None, **kw):
        body = {
            "section": str(self.section.id),
            "date": timezone.localdate().isoformat(),
            "overrides": overrides or {},
        }
        body.update(kw)
        return self.client.post(reverse("attendance-bulk"), body, format="json")

    def _age_marks(self, hours):
        """Pretend the day was first marked `hours` ago (window is anchored on marked_at)."""
        Attendance.objects.update(marked_at=timezone.now() - timedelta(hours=hours))

    def _row(self, i):
        return Attendance.objects.get(enrollment=self.enrollments[i])

    def _eid(self, i):
        return str(self.enrollments[i].id)


class FirstMarkingAndNoopTests(AttendanceEditBase):
    def test_first_marking_needs_no_reason_and_writes_no_log(self):
        r = self._bulk({self._eid(1): "absent"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(Attendance.objects.count(), 4)
        self.assertEqual(AttendanceEditLog.objects.count(), 0)
        self.assertEqual(self._row(1).edit_count, 0)

    def test_resave_without_real_change_needs_no_reason_and_is_not_logged(self):
        self._bulk({self._eid(1): "absent"})
        r = self._bulk({self._eid(1): "absent"})  # identical register, no reason
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["updated"], 0)
        self.assertEqual(AttendanceEditLog.objects.count(), 0)

    def test_unchanged_row_keeps_original_marker_when_another_staff_resaves(self):
        self._bulk()
        other = User.objects.create_user(username="tf_admin2", password="x")
        from .models import StaffProfile
        StaffProfile.objects.create(campus=self.campus, user=other, role=StaffProfile.Role.PRINCIPAL_HOD)
        self.client.force_authenticate(other)
        r = self._bulk()
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self._row(0).marked_by_id, self.teacher.id)


class ReasonTests(AttendanceEditBase):
    def setUp(self):
        super().setUp()
        self._bulk({self._eid(1): "absent"})

    def test_change_without_reason_rejected_and_nothing_changes(self):
        r = self._bulk({self._eid(1): "present"})
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(r.data.get("code"), "reason_required")
        self.assertEqual(self._row(1).status, "absent")
        self.assertEqual(AttendanceEditLog.objects.count(), 0)

    def test_whitespace_only_reason_rejected(self):
        r = self._bulk({self._eid(1): "present"}, reason="   \n ")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(r.data.get("code"), "reason_required")

    def test_too_short_reason_rejected(self):
        r = self._bulk({self._eid(1): "present"}, reason="ab")
        self.assertEqual(r.status_code, 400, r.data)

    def test_too_long_reason_rejected(self):
        r = self._bulk({self._eid(1): "present"}, reason="x" * 201)
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(r.data.get("code"), "reason_too_long")

    def test_edit_with_reason_updates_row_and_writes_audit(self):
        marked_at = self._row(1).marked_at
        r = self._bulk({self._eid(1): "present", self._eid(2): "late"}, reason="  Galti se  absent lag gaya ")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["updated"], 2)
        row = self._row(1)
        self.assertEqual((row.status, row.edit_count), ("present", 1))
        self.assertEqual(row.marked_at, marked_at)  # editing never re-opens the window
        self.assertIsNotNone(row.last_edited_at)

        logs = AttendanceEditLog.objects.order_by("old_status")
        self.assertEqual(logs.count(), 2)
        log = AttendanceEditLog.objects.get(enrollment=self.enrollments[1])
        self.assertEqual((log.old_status, log.new_status, log.action), ("absent", "present", "edit"))
        self.assertEqual(log.reason, "Galti se absent lag gaya")  # whitespace collapsed
        self.assertEqual(log.edited_by_id, self.teacher.id)
        self.assertFalse(log.was_override)
        self.assertEqual(log.campus_id, self.campus.id)
        self.assertEqual(log.section_id, self.section.id)
        self.assertEqual(log.original_marked_at, marked_at)

        audit = CampusAuditLog.objects.get(action="attendance.edit")
        self.assertEqual(audit.metadata["count"], 2)
        self.assertEqual(audit.metadata["reason"], "Galti se absent lag gaya")

    def test_mixed_new_and_changed_rows_only_log_changed(self):
        # A student enrolled after the first save gets a plain create (no reason needed
        # for him) — but the changed old row still needs one.
        late = User.objects.create_user(username="tf_stu_late", password="x")
        from .models import StudentEnrollment
        StudentEnrollment.objects.create(student=late, section=self.section, session=self.session, roll_number="9")
        no_reason = self._bulk({self._eid(1): "present"})
        self.assertEqual(no_reason.status_code, 400, no_reason.data)
        ok = self._bulk({self._eid(1): "present"}, reason="Teacher se galti hui")
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertEqual((ok.data["created"], ok.data["updated"]), (1, 1))
        self.assertEqual(AttendanceEditLog.objects.count(), 1)


class EditWindowTests(AttendanceEditBase):
    def setUp(self):
        super().setUp()
        self._bulk({self._eid(1): "absent"})

    def test_inside_window_teacher_can_edit(self):
        self._age_marks(23)
        r = self._bulk({self._eid(1): "present"}, reason="Galti se tap hua")
        self.assertEqual(r.status_code, 200, r.data)

    def test_after_window_teacher_blocked_403(self):
        self._age_marks(25)
        r = self._bulk({self._eid(1): "present"}, reason="Der se yaad aaya")
        self.assertEqual(r.status_code, 403, r.data)
        self.assertEqual(r.data.get("code"), "edit_window_closed")
        self.assertEqual(self._row(1).status, "absent")
        self.assertEqual(AttendanceEditLog.objects.count(), 0)

    def test_window_is_per_campus_setting(self):
        self.campus.attendance_edit_window_hours = 48
        self.campus.save(update_fields=["attendance_edit_window_hours"])
        self._age_marks(25)
        ok = self._bulk({self._eid(1): "present"}, reason="48h window me hai")
        self.assertEqual(ok.status_code, 200, ok.data)
        self.campus.attendance_edit_window_hours = 1
        self.campus.save(update_fields=["attendance_edit_window_hours"])
        self._age_marks(2)
        blocked = self._bulk({self._eid(1): "absent"}, reason="1h window band")
        self.assertEqual(blocked.status_code, 403, blocked.data)

    def test_locked_window_rejects_whole_save_atomically(self):
        # one old (locked) row + a change on it => nothing at all is written
        self._age_marks(30)
        r = self._bulk({self._eid(1): "present", self._eid(2): "late"}, reason="Dono badalne hain")
        self.assertEqual(r.status_code, 403, r.data)
        self.assertEqual(self._row(2).status, "present")
        self.assertEqual(self._row(1).status, "absent")

    def test_unchanged_resave_after_window_is_still_fine(self):
        self._age_marks(48)
        r = self._bulk({self._eid(1): "absent"})
        self.assertEqual(r.status_code, 200, r.data)

    def test_admin_override_after_window_requires_reason_and_is_flagged(self):
        self._age_marks(72)
        self.client.force_authenticate(self.admin)
        no_reason = self._bulk({self._eid(1): "present"})
        self.assertEqual(no_reason.status_code, 400, no_reason.data)
        self.assertEqual(no_reason.data.get("code"), "reason_required")
        ok = self._bulk({self._eid(1): "present"}, reason="Principal correction after parent complaint")
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertTrue(ok.data["edit_override"])
        log = AttendanceEditLog.objects.get()
        self.assertTrue(log.was_override)
        self.assertEqual(log.edited_by_id, self.admin.id)
        self.assertTrue(CampusAuditLog.objects.get(action="attendance.edit").metadata["override"])


class ClearTests(AttendanceEditBase):
    def setUp(self):
        super().setUp()
        self._bulk({self._eid(0): "absent"})

    def test_clear_requires_reason(self):
        r = self._bulk(clear=True)
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(r.data.get("code"), "reason_required")
        self.assertEqual(Attendance.objects.count(), 4)

    def test_clear_with_reason_logs_every_row_as_clear(self):
        r = self._bulk(clear=True, reason="Undo")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["cleared"], 4)
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertEqual(AttendanceEditLog.objects.filter(action="clear").count(), 4)
        log = AttendanceEditLog.objects.get(enrollment=self.enrollments[0])
        self.assertEqual((log.old_status, log.new_status), ("absent", ""))
        self.assertEqual(CampusAuditLog.objects.filter(action="attendance.clear").count(), 1)

    def test_clear_after_window_blocked_for_teacher_allowed_for_admin(self):
        self._age_marks(30)
        blocked = self._bulk(clear=True, reason="Undo")
        self.assertEqual(blocked.status_code, 403, blocked.data)
        self.assertEqual(Attendance.objects.count(), 4)
        self.client.force_authenticate(self.admin)
        ok = self._bulk(clear=True, reason="Wrong day marked")
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertTrue(AttendanceEditLog.objects.filter(was_override=True).exists())

    def test_clearing_an_unmarked_day_needs_nothing(self):
        r = self._bulk(clear=True, reason="Undo", date=(timezone.localdate() - timedelta(days=3)).isoformat())
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["cleared"], 0)
        r2 = self._bulk(clear=True, date=(timezone.localdate() - timedelta(days=4)).isoformat())  # no reason
        self.assertEqual(r2.status_code, 200, r2.data)


class SingleRecordEndpointTests(AttendanceEditBase):
    def setUp(self):
        super().setUp()
        self._bulk({self._eid(1): "absent"})
        self.row = self._row(1)
        self.url = reverse("attendance-detail", args=[self.row.id])

    def test_patch_status_requires_reason(self):
        r = self.client.patch(self.url, {"status": "present"}, format="json")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(r.data.get("code"), "reason_required")

    def test_patch_status_with_reason_logs_and_hides_reason_in_response(self):
        r = self.client.patch(self.url, {"status": "late", "reason": "Bachcha 10 baje aaya"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertNotIn("reason", r.data)
        self.assertEqual(r.data["edit_count"], 1)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, "late")
        log = AttendanceEditLog.objects.get()
        self.assertEqual((log.old_status, log.new_status, log.reason), ("absent", "late", "Bachcha 10 baje aaya"))

    def test_patch_after_window_blocked(self):
        self._age_marks(25)
        r = self.client.patch(self.url, {"status": "present", "reason": "Der se"}, format="json")
        self.assertEqual(r.status_code, 403, r.data)
        self.assertEqual(r.data.get("code"), "edit_window_closed")

    def test_patch_cannot_move_record_to_other_student_or_date(self):
        r = self.client.patch(self.url, {"enrollment": str(self.enrollments[2].id), "reason": "move"}, format="json")
        self.assertEqual(r.status_code, 400, r.data)
        r = self.client.patch(
            self.url,
            {"date": (timezone.localdate() - timedelta(days=1)).isoformat(), "reason": "move"},
            format="json",
        )
        self.assertEqual(r.status_code, 400, r.data)

    def test_patch_same_status_is_a_noop(self):
        r = self.client.patch(self.url, {"status": "absent"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(AttendanceEditLog.objects.count(), 0)

    def test_delete_requires_reason_and_logs(self):
        no = self.client.delete(self.url)
        self.assertEqual(no.status_code, 400, getattr(no, "data", None))
        yes = self.client.delete(self.url + "?reason=Galat%20student%20mark%20hua")
        self.assertEqual(yes.status_code, 204)
        self.assertFalse(Attendance.objects.filter(pk=self.row.pk).exists())
        log = AttendanceEditLog.objects.get()
        self.assertEqual((log.action, log.old_status, log.reason), ("clear", "absent", "Galat student mark hua"))


class DaySnapshotEditInfoTests(AttendanceEditBase):
    def _day(self):
        return self.client.get(
            reverse("attendance-day"),
            {"section": str(self.section.id), "date": timezone.localdate().isoformat()},
        )

    def test_unmarked_day_is_editable_and_needs_no_reason(self):
        r = self._day()
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(r.data["marked"])
        self.assertTrue(r.data["edit"]["editable"])
        self.assertFalse(r.data["edit"]["reason_required"])
        self.assertEqual(r.data["edit"]["window_hours"], 24)

    def test_marked_day_inside_window(self):
        self._bulk()
        e = self._day().data["edit"]
        self.assertTrue(e["editable"])
        self.assertFalse(e["locked"])
        self.assertTrue(e["reason_required"])
        self.assertIsNotNone(e["editable_until"])
        self.assertFalse(e["can_override"])

    def test_locked_for_teacher_but_editable_for_admin(self):
        self._bulk()
        self._age_marks(25)
        e = self._day().data["edit"]
        self.assertTrue(e["locked"])
        self.assertFalse(e["editable"])
        self.client.force_authenticate(self.admin)
        a = self._day().data["edit"]
        self.assertTrue(a["locked"])
        self.assertTrue(a["can_override"])
        self.assertTrue(a["editable"])

    def test_edit_count_and_last_edited_reported(self):
        self._bulk({self._eid(1): "absent"})
        self._bulk({self._eid(1): "present"}, reason="Galti se absent")
        e = self._day().data["edit"]
        self.assertEqual(e["edit_count"], 1)
        self.assertIsNotNone(e["last_edited_at"])


class EditLogEndpointTests(AttendanceEditBase):
    def setUp(self):
        super().setUp()
        self._bulk({self._eid(1): "absent"})
        self._bulk({self._eid(1): "present"}, reason="Galti se absent")

    def _log(self, **params):
        params.setdefault("section", str(self.section.id))
        return self.client.get(reverse("attendance-edit-log"), params)

    def test_class_teacher_sees_history(self):
        r = self._log(date=timezone.localdate().isoformat(), whole_day="1")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(len(r.data["results"]), 1)
        row = r.data["results"][0]
        self.assertEqual((row["old_status"], row["new_status"], row["reason"]), ("absent", "present", "Galti se absent"))
        self.assertEqual(row["edited_by"]["id"], self.teacher.id)
        self.assertFalse(row["was_override"])

    def test_other_sections_teacher_forbidden(self):
        other = User.objects.create_user(username="tf_other_teacher", password="x")
        from .models import ClassTeacherassigments, StaffProfile
        staff = StaffProfile.objects.create(campus=self.campus, user=other, role=StaffProfile.Role.CLASS_TEACHER)
        ClassTeacherassigments.objects.create(section=self.other_section, staff=staff)
        self.client.force_authenticate(other)
        self.assertEqual(self._log().status_code, 403)

    def test_student_forbidden(self):
        self.client.force_authenticate(self.enrollments[1].student)
        self.assertEqual(self._log().status_code, 403)

    def test_section_param_required(self):
        r = self.client.get(reverse("attendance-edit-log"))
        self.assertEqual(r.status_code, 400)

    def test_subject_filter_separates_whole_day_from_subject(self):
        self._bulk({self._eid(2): "absent"}, subject=str(self.subject.id))
        self._bulk({self._eid(2): "present"}, subject=str(self.subject.id), reason="Maths me galti")
        whole = self._log(whole_day="1").data["results"]
        maths = self._log(subject=str(self.subject.id)).data["results"]
        self.assertEqual(len(whole), 1)
        self.assertEqual(len(maths), 1)
        self.assertEqual(maths[0]["reason"], "Maths me galti")
        self.assertEqual(len(self._log().data["results"]), 2)  # no filter => everything
